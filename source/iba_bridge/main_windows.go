//go:build windows && 386

package main

import (
	"encoding/json"
	"flag"
	"os"
	"path/filepath"
)

func main() {
	mode := flag.String("mode", "check", "check oppure export")
	out := flag.String("out", "", "directory test o file DAT")
	input := flag.String("input", "", "job binario")
	flag.Parse()
	executable, err := os.Executable()
	if err != nil {
		os.Exit(1)
	}
	dll := filepath.Join(filepath.Dir(executable), "ibaFiles.dll")
	if *mode == "check" {
		r := run(dll, *out, newBackend)
		r.NativeWindowsTest = true
		json.NewEncoder(os.Stdout).Encode(r)
		if !r.OK {
			os.Exit(2)
		}
		return
	}
	if *mode != "export" {
		os.Exit(2)
	}
	f, err := os.Open(*input)
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(ExportResult{Native: true, Error: err.Error()})
		os.Exit(2)
	}
	defer f.Close()
	result := exportFile(dll, *out, f, newBackend)
	result.Native = true
	json.NewEncoder(os.Stdout).Encode(result)
	if !result.OK {
		os.Exit(2)
	}
}
