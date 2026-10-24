// Windows GUI launcher: carry a private Python runtime and keep recordings beside this EXE.
package main

import (
	"archive/zip"
	"bytes"
	_ "embed"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"unsafe"
)

//go:embed payload.zip
var payload []byte

const version = "2.0.0"

func message(text string) {
	title, _ := syscall.UTF16PtrFromString("Space Analyzer V" + version)
	body, _ := syscall.UTF16PtrFromString(text)
	syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(body)), uintptr(unsafe.Pointer(title)), 0x10)
}

func extract(target string) error {
	archive, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
	if err != nil {
		return err
	}
	for _, entry := range archive.File {
		name := filepath.FromSlash(entry.Name)
		if !filepath.IsLocal(name) {
			return fmt.Errorf("percorso runtime non valido: %s", entry.Name)
		}
		dest := filepath.Join(target, name)
		if entry.FileInfo().IsDir() {
			if err = os.MkdirAll(dest, 0700); err != nil {
				return err
			}
			continue
		}
		if err = os.MkdirAll(filepath.Dir(dest), 0700); err != nil {
			return err
		}
		source, err := entry.Open()
		if err != nil {
			return err
		}
		file, err := os.OpenFile(dest, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			source.Close()
			return err
		}
		_, copyErr := io.Copy(file, source)
		closeErr := file.Close()
		source.Close()
		if copyErr != nil {
			return copyErr
		}
		if closeErr != nil {
			return closeErr
		}
	}
	return nil
}

func environment(appDir string) []string {
	result := []string{}
	for _, item := range os.Environ() {
		key, _, _ := strings.Cut(item, "=")
		if !strings.EqualFold(key, "OPCUA_APP_DIR") && !strings.EqualFold(key, "PYTHONUNBUFFERED") && !strings.EqualFold(key, "PYTHONUTF8") {
			result = append(result, item)
		}
	}
	return append(result, "OPCUA_APP_DIR="+appDir, "PYTHONUNBUFFERED=1", "PYTHONUTF8=1")
}

func run() error {
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	executable, err = filepath.Abs(executable)
	if err != nil {
		return err
	}
	appDir := filepath.Dir(executable)
	dataDir := os.Getenv("OPCUA_DATA_DIR")
	if dataDir == "" {
		local := os.Getenv("LOCALAPPDATA")
		if local == "" {
			local, err = os.UserConfigDir()
			if err != nil {
				return err
			}
		}
		dataDir = filepath.Join(local, "OPCUA_Web_Client")
	}
	if err = os.MkdirAll(dataDir, 0700); err != nil {
		return err
	}
	logPath := filepath.Join(dataDir, "launcher-v"+version+".log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer logFile.Close()
	fmt.Fprintf(logFile, "\nAvvio V%s; applicativo: %s\n", version, appDir)
	temporary, err := os.MkdirTemp("", "SpaceAnalyzer-v"+version+"-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(temporary)
	if err = extract(temporary); err != nil {
		return fmt.Errorf("estrazione dei servizi: %w", err)
	}
	command := exec.Command(filepath.Join(temporary, "python.exe"), "-u", filepath.Join(temporary, "app.py"))
	command.Dir = temporary
	command.Env = environment(appDir)
	command.Stdout, command.Stderr = logFile, logFile
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	// Python binds the HTTP service before opening the default browser and owns the instance lock.
	if err = command.Run(); err != nil {
		return fmt.Errorf("avvio o esecuzione dei servizi non riusciti: %w\nDettagli: %s", err, logPath)
	}
	fmt.Fprintln(logFile, "Servizi terminati.")
	return nil
}

func main() {
	if err := run(); err != nil {
		message(err.Error())
		os.Exit(1)
	}
}
