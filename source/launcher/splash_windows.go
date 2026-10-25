// Startup splash: the logo and a progress bar on a per-pixel transparent
// layered window, centered on the work area. Every failure only disables it.
package main

import (
	"bytes"
	_ "embed"
	"image"
	"image/color"
	"image/draw"
	"image/png"
	"math"
	"runtime"
	"sync/atomic"
	"syscall"
	"unsafe"
)

//go:embed splash_logo.png
var splashLogo []byte

const (
	splashWidth  = 320
	splashHeight = 300
	barLeft      = 40
	barRight     = 280
	barTop       = 278
	barHeight    = 8

	wsPopup         = 0x80000000
	wsExLayered     = 0x00080000
	wsExTopmost     = 0x00000008
	wsExToolWindow  = 0x00000080
	wsExNoActivate  = 0x08000000
	swShowNoActive  = 4
	wmDestroy       = 0x0002
	wmClose         = 0x0010
	wmTimer         = 0x0113
	wmApp           = 0x8000
	ulwAlpha        = 2
	acSrcOver       = 0
	acSrcAlpha      = 1
	spiGetWorkArea  = 0x0030
	dibRGBColors    = 0
	animationTimer  = 1
	animationPeriod = 15
)

var (
	user32              = syscall.NewLazyDLL("user32.dll")
	gdi32               = syscall.NewLazyDLL("gdi32.dll")
	kernel32            = syscall.NewLazyDLL("kernel32.dll")
	procRegisterClassEx = user32.NewProc("RegisterClassExW")
	procCreateWindowEx  = user32.NewProc("CreateWindowExW")
	procDefWindowProc   = user32.NewProc("DefWindowProcW")
	procDestroyWindow   = user32.NewProc("DestroyWindow")
	procShowWindow      = user32.NewProc("ShowWindow")
	procGetMessage      = user32.NewProc("GetMessageW")
	procTranslateMsg    = user32.NewProc("TranslateMessage")
	procDispatchMsg     = user32.NewProc("DispatchMessageW")
	procPostMessage     = user32.NewProc("PostMessageW")
	procPostQuit        = user32.NewProc("PostQuitMessage")
	procSetTimer        = user32.NewProc("SetTimer")
	procKillTimer       = user32.NewProc("KillTimer")
	procGetDC           = user32.NewProc("GetDC")
	procReleaseDC       = user32.NewProc("ReleaseDC")
	procUpdateLayered   = user32.NewProc("UpdateLayeredWindow")
	procSystemParams    = user32.NewProc("SystemParametersInfoW")
	procCreateCompatDC  = gdi32.NewProc("CreateCompatibleDC")
	procCreateDIB       = gdi32.NewProc("CreateDIBSection")
	procSelectObject    = gdi32.NewProc("SelectObject")
	procDeleteObject    = gdi32.NewProc("DeleteObject")
	procDeleteDC        = gdi32.NewProc("DeleteDC")
	procGetModuleHandle = kernel32.NewProc("GetModuleHandleW")
)

type wndClassEx struct {
	size       uint32
	style      uint32
	wndProc    uintptr
	clsExtra   int32
	wndExtra   int32
	instance   uintptr
	icon       uintptr
	cursor     uintptr
	background uintptr
	menuName   *uint16
	className  *uint16
	iconSmall  uintptr
}

type point struct{ x, y int32 }
type size struct{ cx, cy int32 }
type rect struct{ left, top, right, bottom int32 }

type msg struct {
	hwnd    uintptr
	message uint32
	wParam  uintptr
	lParam  uintptr
	time    uint32
	pt      point
	private uint32
}

type bitmapInfoHeader struct {
	size          uint32
	width         int32
	height        int32
	planes        uint16
	bitCount      uint16
	compression   uint32
	sizeImage     uint32
	xPelsPerMeter int32
	yPelsPerMeter int32
	clrUsed       uint32
	clrImportant  uint32
}

type blendFunction struct{ op, flags, constantAlpha, alphaFormat byte }

// Splash is nil when the window could not be created; its methods accept nil.
type Splash struct {
	hwnd    uintptr
	target  atomic.Uint64 // math.Float64bits of the requested fraction
	current float64
	logo    image.Image
	origin  point
	done    chan struct{}
}

var activeSplash *Splash

func StartSplash() *Splash {
	logo, err := png.Decode(bytes.NewReader(splashLogo))
	if err != nil {
		return nil
	}
	s := &Splash{logo: logo, done: make(chan struct{})}
	ready := make(chan bool)
	go s.run(ready)
	if !<-ready {
		return nil
	}
	return s
}

func (s *Splash) run(ready chan<- bool) {
	runtime.LockOSThread()
	defer close(s.done)
	instance, _, _ := procGetModuleHandle.Call(0)
	className, _ := syscall.UTF16PtrFromString("SpaceAnalyzerSplash")
	class := wndClassEx{wndProc: syscall.NewCallback(splashProc), instance: instance, className: className}
	class.size = uint32(unsafe.Sizeof(class))
	if atom, _, _ := procRegisterClassEx.Call(uintptr(unsafe.Pointer(&class))); atom == 0 {
		ready <- false
		return
	}
	var area rect
	if ok, _, _ := procSystemParams.Call(spiGetWorkArea, 0, uintptr(unsafe.Pointer(&area)), 0); ok == 0 {
		area = rect{0, 0, 1024, 768}
	}
	s.origin = point{(area.left + area.right - splashWidth) / 2, (area.top + area.bottom - splashHeight) / 2}
	title, _ := syscall.UTF16PtrFromString("Space Fieldbus Analyzer")
	s.hwnd, _, _ = procCreateWindowEx.Call(wsExLayered|wsExTopmost|wsExToolWindow|wsExNoActivate,
		uintptr(unsafe.Pointer(className)), uintptr(unsafe.Pointer(title)), wsPopup,
		uintptr(s.origin.x), uintptr(s.origin.y), splashWidth, splashHeight, 0, 0, instance, 0)
	if s.hwnd == 0 || !s.render(0) {
		if s.hwnd != 0 {
			procDestroyWindow.Call(s.hwnd)
		}
		ready <- false
		return
	}
	activeSplash = s
	procShowWindow.Call(s.hwnd, swShowNoActive)
	procSetTimer.Call(s.hwnd, animationTimer, animationPeriod, 0)
	ready <- true
	var m msg
	for {
		result, _, _ := procGetMessage.Call(uintptr(unsafe.Pointer(&m)), 0, 0, 0)
		if int32(result) <= 0 {
			break
		}
		procTranslateMsg.Call(uintptr(unsafe.Pointer(&m)))
		procDispatchMsg.Call(uintptr(unsafe.Pointer(&m)))
	}
	activeSplash = nil
}

func splashProc(hwnd, message, wParam, lParam uintptr) uintptr {
	s := activeSplash
	switch message {
	case wmTimer:
		if s != nil && s.hwnd == hwnd {
			target := math.Float64frombits(s.target.Load())
			if s.current < target {
				// Ease towards the reported stage instead of jumping.
				s.current = math.Min(target, s.current+math.Max(0.004, (target-s.current)*0.18))
				s.render(s.current)
			}
		}
		return 0
	case wmClose:
		procKillTimer.Call(hwnd, animationTimer)
		procDestroyWindow.Call(hwnd)
		return 0
	case wmDestroy:
		procPostQuit.Call(0)
		return 0
	}
	result, _, _ := procDefWindowProc.Call(hwnd, message, wParam, lParam)
	return result
}

// SetProgress accepts a fraction in [0, 1]; it never moves the bar backwards.
func (s *Splash) SetProgress(fraction float64) {
	if s == nil {
		return
	}
	fraction = math.Max(0, math.Min(1, fraction))
	for {
		old := s.target.Load()
		if fraction <= math.Float64frombits(old) || s.target.CompareAndSwap(old, math.Float64bits(fraction)) {
			return
		}
	}
}

// Close lets the bar reach the last reported value, then removes the window.
func (s *Splash) Close() {
	if s == nil {
		return
	}
	procPostMessage.Call(s.hwnd, wmClose, 0, 0)
	<-s.done
}

func (s *Splash) render(fraction float64) bool {
	canvas := image.NewNRGBA(image.Rect(0, 0, splashWidth, splashHeight))
	bounds := s.logo.Bounds()
	offset := image.Pt((splashWidth-bounds.Dx())/2, 4)
	draw.Draw(canvas, bounds.Sub(bounds.Min).Add(offset), s.logo, bounds.Min, draw.Over)
	fill := barLeft + int(math.Round(fraction*float64(barRight-barLeft)))
	for x := barLeft; x < barRight; x++ {
		for y := barTop; y < barTop+barHeight; y++ {
			c := color.NRGBA{60, 60, 60, 150}
			if x < fill {
				c = color.NRGBA{253, 101, 2, 255}
			}
			if corner(x, y) {
				c.A /= 3
			}
			canvas.SetNRGBA(x, y, c)
		}
	}
	return s.present(canvas)
}

func corner(x, y int) bool {
	edgeX := x == barLeft || x == barRight-1
	edgeY := y == barTop || y == barTop+barHeight-1
	return edgeX && edgeY
}

func (s *Splash) present(canvas *image.NRGBA) bool {
	screen, _, _ := procGetDC.Call(0)
	if screen == 0 {
		return false
	}
	defer procReleaseDC.Call(0, screen)
	memory, _, _ := procCreateCompatDC.Call(screen)
	if memory == 0 {
		return false
	}
	defer procDeleteDC.Call(memory)
	header := bitmapInfoHeader{width: splashWidth, height: -splashHeight, planes: 1, bitCount: 32}
	header.size = uint32(unsafe.Sizeof(header))
	var bits unsafe.Pointer
	bitmap, _, _ := procCreateDIB.Call(screen, uintptr(unsafe.Pointer(&header)), dibRGBColors, uintptr(unsafe.Pointer(&bits)), 0, 0)
	if bitmap == 0 || bits == nil {
		return false
	}
	defer procDeleteObject.Call(bitmap)
	// UpdateLayeredWindow expects premultiplied BGRA.
	pixels := unsafe.Slice((*byte)(bits), splashWidth*splashHeight*4)
	for i := 0; i < len(canvas.Pix); i += 4 {
		r, g, b, a := uint32(canvas.Pix[i]), uint32(canvas.Pix[i+1]), uint32(canvas.Pix[i+2]), uint32(canvas.Pix[i+3])
		pixels[i], pixels[i+1], pixels[i+2], pixels[i+3] = byte(b*a/255), byte(g*a/255), byte(r*a/255), byte(a)
	}
	previous, _, _ := procSelectObject.Call(memory, bitmap)
	defer procSelectObject.Call(memory, previous)
	dimensions := size{splashWidth, splashHeight}
	source := point{0, 0}
	blend := blendFunction{acSrcOver, 0, 255, acSrcAlpha}
	ok, _, _ := procUpdateLayered.Call(s.hwnd, screen, uintptr(unsafe.Pointer(&s.origin)), uintptr(unsafe.Pointer(&dimensions)),
		memory, uintptr(unsafe.Pointer(&source)), 0, uintptr(unsafe.Pointer(&blend)), ulwAlpha)
	return ok != 0
}
