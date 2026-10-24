package main

import (
	"errors"
	"math"
	"os"
	"path/filepath"
	"testing"
)

type fakeBackend struct {
	licensed         bool
	writes, released int
	fail             string
	values           []float32
	period           float32
}

func (f *fakeBackend) Version() (string, error) { return "fake: not native execution", nil }
func (f *fakeBackend) Licensed() (bool, error) {
	if f.fail == "license" {
		return false, errors.New("simulated license error")
	}
	return f.licensed, nil
}
func (f *fakeBackend) WriteFixture(path string, values []float32, period float32) error {
	f.writes++
	if f.fail == "write" {
		return errors.New("simulated write error")
	}
	f.values = append([]float32(nil), values...)
	f.period = period
	return os.WriteFile(path, []byte("FAKE-NOT-IBA-DAT"), 0600)
}
func (f *fakeBackend) ReadFixture(string) (Dataset, error) {
	if f.fail == "read" {
		return Dataset{}, errors.New("simulated read error")
	}
	values := append([]float32(nil), f.values...)
	if f.fail == "mismatch" {
		values[127] += 1
	}
	period := f.period
	if f.fail == "timebase" {
		period = 0.01
	}
	return Dataset{Values: values, Timebase: period}, nil
}
func (f *fakeBackend) Release() { f.released++ }

func testDLL(t *testing.T) string {
	t.Helper()
	for _, path := range []string{os.Getenv("IBAFILES_DLL"), "../../upload/ibaFiles.dll", "../ibaFiles.dll"} {
		if path != "" {
			if _, err := os.Stat(path); err == nil {
				absolute, _ := filepath.Abs(path)
				return absolute
			}
		}
	}
	t.Fatal("DLL fixture missing: set IBAFILES_DLL to the supplied DLL")
	return ""
}

func TestUnlicensedDoesNotCreateDAT(t *testing.T) {
	f := &fakeBackend{}
	result := run(testDLL(t), t.TempDir(), func(string) (Backend, error) { return f, nil })
	if result.OK || result.Phase != "license" || f.writes != 0 || result.DATPath != "" || f.released != 1 {
		t.Fatalf("Unlicensed DLL must never enter write: %+v; backend %+v", result, f)
	}
}

func TestLicenseErrorDoesNotCreateDAT(t *testing.T) {
	f := &fakeBackend{licensed: true, fail: "license"}
	result := run(testDLL(t), t.TempDir(), func(string) (Backend, error) { return f, nil })
	if result.OK || result.Error != "simulated license error" || f.writes != 0 || f.released != 1 {
		t.Fatal(result)
	}
}

func TestExactRoundtripAndUniqueDestination(t *testing.T) {
	f := &fakeBackend{licensed: true}
	destination := t.TempDir()
	factory := func(string) (Backend, error) { return f, nil }
	one := run(testDLL(t), destination, factory)
	two := run(testDLL(t), destination, factory)
	if !one.OK || !two.OK || one.DATPath == two.DATPath || !one.Float32Exact || !one.TimebaseMatches || one.SamplesRead != 512 || one.SamplesWritten != 512 || f.released != 2 {
		t.Fatalf("%+v %+v", one, two)
	}
	if math.Float32bits(one.PeriodSeconds) != math.Float32bits(float32(0.001)) {
		t.Fatal("timebase changed")
	}
	if one.NativeWindowsTest {
		t.Fatal("A fake backend cannot claim a native Windows test")
	}
}

func TestIncompleteOrModifiedReadBackFails(t *testing.T) {
	for _, fail := range []string{"write", "read", "mismatch", "timebase"} {
		t.Run(fail, func(t *testing.T) {
			f := &fakeBackend{licensed: true, fail: fail}
			result := run(testDLL(t), t.TempDir(), func(string) (Backend, error) { return f, nil })
			if result.OK || result.Error == "" || f.released != 1 {
				t.Fatal(result)
			}
		})
	}
}

func TestUnknownDLLNeverLoaded(t *testing.T) {
	path := filepath.Join(t.TempDir(), "other.dll")
	os.WriteFile(path, []byte("not the supplied DLL"), 0600)
	called := false
	result := run(path, t.TempDir(), func(string) (Backend, error) { called = true; return nil, nil })
	if called || result.OK || result.Phase != "verify_dll" || result.DLLSHA256 == "" {
		t.Fatal(result)
	}
}

func TestLoaderFailureIsReported(t *testing.T) {
	result := run(testDLL(t), t.TempDir(), func(string) (Backend, error) { return nil, errors.New("simulated loader failure") })
	if result.OK || result.Phase != "load_dll" || result.Error != "simulated loader failure" {
		t.Fatal(result)
	}
}
