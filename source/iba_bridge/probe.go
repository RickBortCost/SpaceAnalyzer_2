// Package main provides an isolated test of the exact ibaFiles DLL supplied by the user.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"time"
)

const expectedHash = "63c86505734e56aaac6eb0e845c9a0bad16e55c12abd7922754157fdcb12469b"
const sampleCount = 512

type Result struct {
	ToolVersion       string   `json:"tool_version"`
	StartedUTC        string   `json:"started_utc"`
	OS                string   `json:"os"`
	Architecture      string   `json:"architecture"`
	DLLPath           string   `json:"dll_path"`
	DLLSHA256         string   `json:"dll_sha256,omitempty"`
	WriterVersion     string   `json:"writer_version,omitempty"`
	Licensed          *bool    `json:"licensed,omitempty"`
	Phase             string   `json:"phase"`
	OK                bool     `json:"ok"`
	Error             string   `json:"error,omitempty"`
	DATPath           string   `json:"dat_path,omitempty"`
	DATBytes          int64    `json:"dat_bytes,omitempty"`
	DATPrefixHex      string   `json:"dat_prefix_hex,omitempty"`
	PeriodSeconds     float32  `json:"period_seconds,omitempty"`
	SamplesWritten    int      `json:"samples_written"`
	SamplesRead       int      `json:"samples_read"`
	Float32Exact      bool     `json:"float32_exact"`
	TimebaseMatches   bool     `json:"timebase_matches"`
	NativeWindowsTest bool     `json:"native_windows_test"`
	Notes             []string `json:"notes"`
	FinishedUTC       string   `json:"finished_utc,omitempty"`
}

type Dataset struct {
	Values   []float32
	Timebase float32
	XOffset  float32
}

type Backend interface {
	Version() (string, error)
	Licensed() (bool, error)
	WriteFixture(string, []float32, float32) error
	ReadFixture(string) (Dataset, error)
	Release()
}

type BackendFactory func(string) (Backend, error)

func verifyDLL(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", fmt.Errorf("Impossibile leggere ibaFiles.dll: %w", err)
	}
	defer f.Close()
	h := sha256.New()
	n, err := io.Copy(h, io.LimitReader(f, 16*1024*1024+1))
	if err != nil {
		return "", err
	}
	digest := hex.EncodeToString(h.Sum(nil))
	if n != 180224 || digest != expectedHash {
		return digest, errors.New("DLL diversa da quella analizzata: test interrotto prima del caricamento. Le API di questo test sono specifiche della versione 3.2.0 ricevuta")
	}
	return digest, nil
}

// run performs the stateful check without assuming that the DLL contains a license.
// There are no registration calls, elevated operations or acquisition connections.
func run(dllPath, destination string, factory BackendFactory) (result Result) {
	result = Result{ToolVersion: "1.0.0", StartedUTC: time.Now().UTC().Format(time.RFC3339Nano),
		OS: runtime.GOOS, Architecture: runtime.GOARCH, DLLPath: dllPath, Phase: "verify_dll",
		Notes: []string{
			"Test con 512 valori sintetici Float32 e base temporale di 1 ms; non misura prestazioni o acquisizione da PLC.",
			"Lettura di controllo eseguita con la stessa DLL; compatibilita con la versione ibaPDA/ibaAnalyzer dell'utente da verificare aprendo il DAT.",
			"Questo test non registra la DLL e non modifica Space Analyzer o le registrazioni esistenti.",
		}}
	defer func() { result.FinishedUTC = time.Now().UTC().Format(time.RFC3339Nano) }()
	var err error
	result.DLLSHA256, err = verifyDLL(dllPath)
	if err != nil {
		result.Error = err.Error()
		return
	}
	result.Phase = "load_dll"
	backend, err := factory(dllPath)
	if err != nil {
		result.Error = err.Error()
		return
	}
	defer backend.Release()
	result.Phase = "writer_version"
	result.WriterVersion, err = backend.Version()
	if err != nil {
		result.Error = err.Error()
		return
	}
	result.Phase = "license"
	licensed, err := backend.Licensed()
	if err != nil {
		result.Error = err.Error()
		return
	}
	result.Licensed = &licensed
	if !licensed {
		result.Error = "La DLL segnala Licensed = false: licenza di scrittura non disponibile o non rilevata. Nessun file DAT creato"
		return
	}
	result.Phase = "prepare_destination"
	if err = os.MkdirAll(destination, 0700); err != nil {
		result.Error = "Cartella di test non scrivibile: " + err.Error()
		return
	}
	// A unique directory ensures that a previous DAT is never replaced.
	folder, err := os.MkdirTemp(destination, "Test_DAT_")
	if err != nil {
		result.Error = err.Error()
		return
	}
	result.DATPath = filepath.Join(folder, "Space_Analyzer_1ms.dat")
	values := make([]float32, sampleCount)
	for i := range values {
		values[i] = float32(i-256) * 0.125
	}
	period := float32(0.001)
	result.Phase = "write"
	if err = backend.WriteFixture(result.DATPath, values, period); err != nil {
		result.Error = err.Error()
		return
	}
	result.SamplesWritten = len(values)
	result.Phase = "read_back"
	actual, err := backend.ReadFixture(result.DATPath)
	if err != nil {
		result.Error = err.Error()
		return
	}
	result.PeriodSeconds = actual.Timebase
	result.SamplesRead = len(actual.Values)
	result.TimebaseMatches = math.Float32bits(actual.Timebase) == math.Float32bits(period)
	result.Float32Exact = len(actual.Values) == len(values)
	if result.Float32Exact {
		for i, v := range values {
			if math.Float32bits(v) != math.Float32bits(actual.Values[i]) {
				result.Float32Exact = false
				break
			}
		}
	}
	if !result.TimebaseMatches || !result.Float32Exact || actual.XOffset != 0 {
		result.Error = fmt.Sprintf("Verifica DAT fallita: campioni=%d/%d, valori Float32 esatti=%t, base temporale=%g s, offset=%g s", len(actual.Values), len(values), result.Float32Exact, actual.Timebase, actual.XOffset)
		return
	}
	info, err := os.Stat(result.DATPath)
	if err != nil || info.Size() == 0 {
		result.Error = "File DAT assente o vuoto dopo la scrittura"
		return
	}
	result.DATBytes = info.Size()
	f, err := os.Open(result.DATPath)
	if err != nil {
		result.Error = err.Error()
		return
	}
	buf := make([]byte, 16)
	n, _ := f.Read(buf)
	f.Close()
	result.DATPrefixHex = hex.EncodeToString(buf[:n])
	result.Phase, result.OK = "complete", true
	return
}
