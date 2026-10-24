package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"testing"
)

// Native COM is never loaded by these tests. All results remain Native=false.
type datasetFake struct {
	fakeBackend
	channels map[string]Dataset
}

func (f *datasetFake) WriteDataset(path string, h Header, r io.Reader) error {
	f.writes++
	f.channels = map[string]Dataset{}
	for _, c := range h.Channels {
		values := make([]float32, c.Count)
		if err := binary.Read(r, binary.LittleEndian, values); err != nil {
			return err
		}
		f.channels[c.Name] = Dataset{Values: values, Timebase: c.Period, XOffset: c.Offset}
	}
	return os.WriteFile(path, []byte("TEST MOCK NOT DAT"), 0600)
}
func (f *datasetFake) ReadChannel(path, name string, n int) (Dataset, error) {
	return f.channels[name], nil
}
func job(t *testing.T, h Header, values []float32) *os.File {
	t.Helper()
	raw, _ := json.Marshal(h)
	p := filepath.Join(t.TempDir(), "job")
	var b bytes.Buffer
	binary.Write(&b, binary.LittleEndian, uint32(len(raw)))
	b.Write(raw)
	binary.Write(&b, binary.LittleEndian, values)
	if err := os.WriteFile(p, b.Bytes(), 0600); err != nil {
		t.Fatal(err)
	}
	file, err := os.Open(p)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { file.Close() })
	return file
}
func TestExportFramingLicenseAndExactReadback(t *testing.T) {
	h := Header{Channels: []Channel{{Name: "A", Count: 3, Period: .001}, {Name: "A_Q", Count: 3, Period: .001}}}
	for _, licensed := range []bool{false, true} {
		f := &datasetFake{fakeBackend: fakeBackend{licensed: licensed}}
		result := exportFile(testDLL(t), filepath.Join(t.TempDir(), "out.dat"), job(t, h, []float32{1, 2, 3, 3, 3, 3}), func(string) (Backend, error) { return f, nil })
		if result.OK != licensed || result.Native || result.Roundtrip != licensed {
			t.Fatalf("result %+v", result)
		}
		if !licensed && f.writes != 0 {
			t.Fatal("Unlicensed write attempted")
		}
	}
}
func TestExportRejectsTruncatedBodyAndExistingFile(t *testing.T) {
	h := Header{Channels: []Channel{{Name: "A", Count: 3, Period: .001}}}
	f := &datasetFake{fakeBackend: fakeBackend{licensed: true}}
	factory := func(string) (Backend, error) { return f, nil }
	out := filepath.Join(t.TempDir(), "out.dat")
	r := exportFile(testDLL(t), out, job(t, h, []float32{1}), factory)
	if r.OK {
		t.Fatal("Truncated body accepted")
	}
	os.WriteFile(out, []byte("original"), 0600)
	r = exportFile(testDLL(t), out, job(t, h, []float32{1, 2, 3}), factory)
	if r.OK {
		t.Fatal("Overwrite accepted")
	}
	raw, _ := os.ReadFile(out)
	if string(raw) != "original" {
		t.Fatal("Original overwritten")
	}
}
func TestHeaderBounds(t *testing.T) {
	for _, h := range []Header{{}, {Channels: []Channel{{Name: "A", Count: 0, Period: 1}}}, {Channels: []Channel{{Name: "A", Count: 1, Period: 0}}},
		{Channels: []Channel{{Name: "A", Count: 1, Period: 1}, {Name: "A", Count: 1, Period: 1}}}} {
		if _, err := readHeader(job(t, h, nil)); err == nil {
			t.Fatal("Invalid header accepted")
		}
	}
	var raw bytes.Buffer
	binary.Write(&raw, binary.LittleEndian, uint32(1024*1024+1))
	if _, err := readHeader(&raw); err == nil {
		t.Fatal("Oversized header accepted")
	}
}
