//go:build windows && 386

package main

import (
	"bytes"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"syscall"
	"unicode/utf16"
	"unsafe"
)

type guid struct {
	A    uint32
	B, C uint16
	D    [8]byte
}

var (
	iidFactory  = guid{1, 0, 0, [8]byte{0xc0, 0, 0, 0, 0, 0, 0, 0x46}}
	classWriter = guid{0xb9f36d49, 0x6105, 0x45e4, [8]byte{0x81, 0xdb, 0x4f, 0xa7, 0x65, 0xe8, 0x33, 0x87}}
	iidWriter2  = guid{0x77d045a3, 0x4d8c, 0x4640, [8]byte{0x8c, 0xfe, 0xab, 0x0b, 0x59, 0x39, 0xe0, 0xdd}}
	classReader = guid{0x10923331, 0x8b96, 0x489a, [8]byte{0xa6, 0x37, 0xc7, 0x1a, 0x0b, 0x79, 0x58, 0x68}}
	iidReader   = guid{0x1c2a55b3, 0x01bb, 0x45ec, [8]byte{0xa8, 0xb8, 0x15, 0xb1, 0xfe, 0xe5, 0xd5, 0xc2}}
	com         = systemDLL("ole32.dll")
	auto        = systemDLL("oleaut32.dll")
	kernel      = systemDLL("kernel32.dll")
)

func systemDLL(name string) *syscall.LazyDLL {
	return syscall.NewLazyDLL(filepath.Join(os.Getenv("SystemRoot"), "System32", name))
}

//go:uintptrescapes
func invoke(object uintptr, slot int, args ...uintptr) (uintptr, error) {
	if object == 0 {
		return 0, errors.New("Interfaccia COM nulla")
	}
	vtable := *(*uintptr)(unsafe.Pointer(object))
	address := *(*uintptr)(unsafe.Pointer(vtable + uintptr(slot)*4))
	parameters := append([]uintptr{object}, args...)
	code, _, _ := syscall.SyscallN(address, parameters...)
	if int32(code) < 0 {
		return code, fmt.Errorf("COM slot %d: HRESULT 0x%08X", slot, uint32(code))
	}
	return code, nil
}

func release(object uintptr) {
	if object != 0 {
		_, _ = invoke(object, 2)
	}
}

func bstr(text string) (uintptr, error) {
	chars := utf16.Encode([]rune(text))
	chars = append(chars, 0)
	p, _, _ := auto.NewProc("SysAllocStringLen").Call(uintptr(unsafe.Pointer(&chars[0])), uintptr(len(chars)-1))
	runtime.KeepAlive(chars)
	if p == 0 {
		return 0, errors.New("Allocazione BSTR fallita")
	}
	return p, nil
}

func freeString(p uintptr) {
	if p != 0 {
		auto.NewProc("SysFreeString").Call(p)
	}
}

func stringValue(p uintptr) string {
	if p == 0 {
		return ""
	}
	n, _, _ := auto.NewProc("SysStringLen").Call(p)
	if n > 65536 {
		return "[BSTR troppo lunga]"
	}
	return string(utf16.Decode(unsafe.Slice((*uint16)(unsafe.Pointer(p)), int(n))))
}

type nativeBackend struct {
	dll         *syscall.DLL
	writer      uintptr
	initialized bool
}

func newBackend(path string) (Backend, error) {
	if _, err := verifyDLL(path); err != nil {
		return nil, err
	}
	runtime.LockOSThread()
	backend := &nativeBackend{}
	code, _, _ := com.NewProc("CoInitializeEx").Call(0, 2)
	if int32(code) < 0 {
		runtime.UnlockOSThread()
		return nil, fmt.Errorf("CoInitializeEx: HRESULT 0x%08X", uint32(code))
	}
	backend.initialized = true
	path16, err := syscall.UTF16PtrFromString(path)
	if err != nil {
		backend.Release()
		return nil, err
	}
	// The exact supplied DLL and Windows system dependencies are the only search locations.
	handle, _, loadErr := kernel.NewProc("LoadLibraryExW").Call(uintptr(unsafe.Pointer(path16)), 0, 0x100|0x800)
	if handle == 0 {
		backend.Release()
		return nil, fmt.Errorf("Caricamento DLL x86 fallito: %v", loadErr)
	}
	backend.dll = &syscall.DLL{Name: path, Handle: syscall.Handle(handle)}
	backend.writer, err = backend.create(&classWriter, &iidWriter2)
	if err != nil {
		backend.Release()
		return nil, fmt.Errorf("IbaFileWriter2: %w", err)
	}
	return backend, nil
}

func (b *nativeBackend) create(class, iid *guid) (uintptr, error) {
	proc, err := b.dll.FindProc("DllGetClassObject")
	if err != nil {
		return 0, err
	}
	var factory, object uintptr
	code, _, _ := proc.Call(uintptr(unsafe.Pointer(class)), uintptr(unsafe.Pointer(&iidFactory)), uintptr(unsafe.Pointer(&factory)))
	if int32(code) < 0 {
		return 0, fmt.Errorf("DllGetClassObject: HRESULT 0x%08X", uint32(code))
	}
	defer release(factory)
	_, err = invoke(factory, 3, 0, uintptr(unsafe.Pointer(iid)), uintptr(unsafe.Pointer(&object)))
	if err != nil {
		return 0, err
	}
	if object == 0 {
		return 0, errors.New("CreateInstance non ha restituito un'interfaccia")
	}
	return object, nil
}

func (b *nativeBackend) Version() (string, error) {
	var value uintptr
	_, err := invoke(b.writer, 4, uintptr(unsafe.Pointer(&value)))
	defer freeString(value)
	return stringValue(value), err
}

func (b *nativeBackend) Licensed() (bool, error) {
	var value int32
	_, err := invoke(b.writer, 3, uintptr(unsafe.Pointer(&value)))
	return value != 0, err
}

func (b *nativeBackend) WriteFixture(path string, values []float32, period float32) (err error) {
	var input bytes.Buffer
	if err = binary.Write(&input, binary.LittleEndian, values); err != nil {
		return
	}
	return b.WriteDataset(path, Header{Channels: []Channel{{Name: "Space_Analyzer_Ramp_Test", Count: len(values), Period: period}}}, &input)
}

func infoField(object uintptr, slot int, key, value string) error {
	k, err := bstr(key)
	if err != nil {
		return err
	}
	defer freeString(k)
	v, err := bstr(value)
	if err != nil {
		return err
	}
	defer freeString(v)
	_, err = invoke(object, slot, k, v)
	return err
}

func writeBlock(channel uintptr, raw []byte) (err error) {
	n := len(raw) / 4
	array, _, _ := auto.NewProc("SafeArrayCreateVector").Call(4, 0, uintptr(n))
	if array == 0 {
		return errors.New("SafeArrayCreateVector fallita")
	}
	defer auto.NewProc("SafeArrayDestroy").Call(array)
	var pointer uintptr
	code, _, _ := auto.NewProc("SafeArrayAccessData").Call(array, uintptr(unsafe.Pointer(&pointer)))
	if int32(code) < 0 || pointer == 0 {
		return errors.New("SafeArrayAccessData fallita")
	}
	copy(unsafe.Slice((*byte)(unsafe.Pointer(pointer)), len(raw)), raw)
	auto.NewProc("SafeArrayUnaccessData").Call(array)
	// WriteDataV accepts a 16-byte x86 VARIANT BY VALUE (four DWORDs), not a pointer.
	_, err = invoke(channel, 9, 0x2004, 0, array, 0)
	return
}

func (b *nativeBackend) WriteDataset(path string, h Header, input io.Reader) (err error) {
	filename, err := bstr(path)
	if err != nil {
		return err
	}
	defer freeString(filename)
	// FALSE explicitly prohibits replacing an existing data file.
	if _, err = invoke(b.writer, 5, filename, 0); err != nil {
		return fmt.Errorf("Create: %w", err)
	}
	defer func() {
		_, e := invoke(b.writer, 6)
		if err == nil {
			err = e
		}
	}()
	if _, err = invoke(b.writer, 14, 1); err != nil {
		return fmt.Errorf("EnableCompression: %w", err)
	}
	for key, value := range h.Info {
		if err = infoField(b.writer, 7, key, value); err != nil {
			return
		}
	}
	for index, c := range h.Channels {
		if err = b.writeChannel(index, c, input); err != nil {
			return fmt.Errorf("Canale %s: %w", c.Name, err)
		}
	}
	return nil
}

func (b *nativeBackend) writeChannel(index int, c Channel, input io.Reader) (err error) {
	name, err := bstr(c.Name)
	if err != nil {
		return
	}
	defer freeString(name)
	var channel uintptr
	_, err = invoke(b.writer, 8, uintptr(index/16), uintptr(index%16), name, uintptr(math.Float32bits(c.Period)), uintptr(unsafe.Pointer(&channel)))
	if err != nil {
		return
	}
	if channel == 0 {
		return errors.New("Canale COM nullo")
	}
	defer release(channel)
	if _, err = invoke(channel, 6, uintptr(math.Float32bits(c.Offset))); err != nil {
		return
	}
	for key, value := range map[string]string{"unit": c.Unit, "comment": c.NodeID} {
		if err = infoField(channel, 7, key, value); err != nil {
			return
		}
	}
	for left := c.Count; left > 0; {
		n := left
		if n > 8192 {
			n = 8192
		}
		raw := make([]byte, n*4)
		if _, err = io.ReadFull(input, raw); err != nil {
			return
		}
		for i := 0; i < len(raw); i += 4 {
			v := math.Float32frombits(binary.LittleEndian.Uint32(raw[i:]))
			if math.IsInf(float64(v), 0) || math.IsNaN(float64(v)) {
				return errors.New("Campione non finito")
			}
		}
		if err = writeBlock(channel, raw); err != nil {
			return
		}
		left -= n
	}
	return
}

// A native x86 VARIANT is 16 bytes; SAFEARRAY pointers occupy the first union DWORD.
type variant struct {
	VT, R1, R2, R3 uint16
	Data           [2]uint32
}

func (b *nativeBackend) ReadFixture(path string) (result Dataset, err error) {
	return b.ReadChannel(path, "Space_Analyzer_Ramp_Test", sampleCount)
}

func (b *nativeBackend) ReadChannel(path, channelName string, expected int) (result Dataset, err error) {
	reader, err := b.create(&classReader, &iidReader)
	if err != nil {
		return result, err
	}
	defer release(reader)
	filename, err := bstr(path)
	if err != nil {
		return result, err
	}
	defer freeString(filename)
	if _, err = invoke(reader, 4, filename); err != nil {
		return result, fmt.Errorf("Reader.Open: %w", err)
	}
	defer func() {
		_, e := invoke(reader, 5)
		if err == nil {
			err = e
		}
	}()
	name, err := bstr(channelName)
	if err != nil {
		return result, err
	}
	defer freeString(name)
	var channel uintptr
	if _, err = invoke(reader, 11, name, uintptr(unsafe.Pointer(&channel))); err != nil {
		return result, fmt.Errorf("QueryChannelByName: %w", err)
	}
	if channel == 0 {
		return result, errors.New("Canale di lettura assente")
	}
	defer release(channel)
	var data variant
	if _, err = invoke(channel, 10, uintptr(unsafe.Pointer(&result.Timebase)), uintptr(unsafe.Pointer(&result.XOffset)), uintptr(unsafe.Pointer(&data))); err != nil {
		return result, fmt.Errorf("QueryTimebasedData: %w", err)
	}
	defer auto.NewProc("VariantClear").Call(uintptr(unsafe.Pointer(&data)))
	if data.VT != 0x2004 {
		return result, fmt.Errorf("Tipo di lettura inatteso: VARIANT VT=0x%04X; atteso SAFEARRAY Float32", data.VT)
	}
	array := uintptr(data.Data[0])
	if array == 0 {
		return result, errors.New("SAFEARRAY nulla")
	}
	dimensions, _, _ := auto.NewProc("SafeArrayGetDim").Call(array)
	if dimensions != 1 {
		return result, fmt.Errorf("Dimensioni SAFEARRAY inattese: %d", dimensions)
	}
	var lower, upper int32
	for _, item := range []struct {
		name string
		out  *int32
	}{{"SafeArrayGetLBound", &lower}, {"SafeArrayGetUBound", &upper}} {
		code, _, _ := auto.NewProc(item.name).Call(array, 1, uintptr(unsafe.Pointer(item.out)))
		if int32(code) < 0 {
			return result, fmt.Errorf("%s: HRESULT 0x%08X", item.name, uint32(code))
		}
	}
	count := int64(upper) - int64(lower) + 1
	if count != int64(expected) || count < 1 || count > 2000000 {
		return result, fmt.Errorf("Numero campioni inatteso: %d; attesi %d", count, expected)
	}
	var pointer uintptr
	code, _, _ := auto.NewProc("SafeArrayAccessData").Call(array, uintptr(unsafe.Pointer(&pointer)))
	if int32(code) < 0 || pointer == 0 {
		return result, fmt.Errorf("SafeArrayAccessData: HRESULT 0x%08X", uint32(code))
	}
	defer auto.NewProc("SafeArrayUnaccessData").Call(array)
	result.Values = append([]float32(nil), unsafe.Slice((*float32)(unsafe.Pointer(pointer)), int(count))...)
	return result, nil
}

func (b *nativeBackend) Release() {
	if b.writer != 0 {
		release(b.writer)
		b.writer = 0
	}
	if b.dll != nil {
		b.dll.Release()
		b.dll = nil
	}
	if b.initialized {
		com.NewProc("CoUninitialize").Call()
		b.initialized = false
		runtime.UnlockOSThread()
	}
}
