// Windows GUI launcher: carry the self-contained .NET service (SpaceAnalyzer.exe) and keep recordings beside this EXE.
package main

import (
	"archive/zip"
	"bufio"
	"bytes"
	_ "embed"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unsafe"
)

// The C# service prints this marker for each startup stage (only with SPACE_SPLASH=1).
const startupMarker = "@@SPACE_ANALYZER_STARTUP "

//go:embed payload.zip
var payload []byte

// SHA-256 of payload.zip, written by the build: the extracted runtime is cached per payload.
//
//go:embed payload.sha256
var payloadDigest string

const version = "3.15.0"

func message(text string) {
	title, _ := syscall.UTF16PtrFromString("Space Fieldbus Analyzer V" + version)
	body, _ := syscall.UTF16PtrFromString(text)
	syscall.NewLazyDLL("user32.dll").NewProc("MessageBoxW").Call(0, uintptr(unsafe.Pointer(body)), uintptr(unsafe.Pointer(title)), 0x10)
}

func extract(target string, progress func(float64)) error {
	archive, err := zip.NewReader(bytes.NewReader(payload), int64(len(payload)))
	if err != nil {
		return err
	}
	var total, done uint64
	for _, entry := range archive.File {
		total += entry.UncompressedSize64
	}
	for _, entry := range archive.File {
		done += entry.UncompressedSize64
		if total > 0 {
			progress(float64(done) / float64(total))
		}
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

func environment(appDir, runtimeDir, dataDir string) []string {
	result := []string{}
	for _, item := range os.Environ() {
		key, _, _ := strings.Cut(item, "=")
		if !strings.EqualFold(key, "OPCUA_APP_DIR") && !strings.EqualFold(key, "SPACE_BASE_DIR") && !strings.EqualFold(key, "SPACE_SPLASH") && !strings.EqualFold(key, "SPACE_PACKAGED") && !strings.EqualFold(key, "OPCUA_DATA_DIR") &&
			!strings.EqualFold(key, "TMP") && !strings.EqualFold(key, "TEMP") {
			result = append(result, item)
		}
	}
	// Temporary files of .NET, SQLite and the OPC UA stack stay on the disk of the data folder.
	temp := filepath.Join(dataDir, "tmp")
	os.MkdirAll(temp, 0700)
	return append(result, "TMP="+temp, "TEMP="+temp, "OPCUA_DATA_DIR="+dataDir, "OPCUA_APP_DIR="+appDir, "SPACE_BASE_DIR="+runtimeDir, "SPACE_PACKAGED=1", "SPACE_SPLASH=1", "DOTNET_CLI_TELEMETRY_OPTOUT=1")
}

// dataDirectory chooses where settings, logs, the extracted service and every cache live:
// OPCUA_DATA_DIR when set; otherwise SpaceAnalyzer_Data beside the EXE when that disk can be
// written, so nothing grows on C: when the EXE runs from another disk; otherwise the 2.x folder
// %LOCALAPPDATA%\OPCUA_Web_Client. legacy is that 2.x folder when data now live beside the EXE.
func dataDirectory(appDir string) (dir string, legacy string, err error) {
	if configured := os.Getenv("OPCUA_DATA_DIR"); configured != "" {
		return configured, "", os.MkdirAll(configured, 0700)
	}
	local := os.Getenv("LOCALAPPDATA")
	if local == "" {
		if local, err = os.UserConfigDir(); err != nil {
			return "", "", err
		}
	}
	legacy = filepath.Join(local, "OPCUA_Web_Client")
	beside := filepath.Join(appDir, "SpaceAnalyzer_Data")
	if writable(beside) {
		return beside, legacy, nil
	}
	return legacy, "", os.MkdirAll(legacy, 0700)
}

func writable(dir string) bool {
	if os.MkdirAll(dir, 0700) != nil {
		return false
	}
	probe, err := os.CreateTemp(dir, ".write-test-")
	if err != nil {
		return false
	}
	probe.Close()
	return os.Remove(probe.Name()) == nil
}

// migrate brings the settings of the 2.x/3.0 data folder beside the EXE once (they stay in the
// old folder for older versions), moves imported recordings and removes the old caches, which
// can be large (Live originals, calculations, extracted services).
func migrate(legacy, target string, log io.Writer) {
	marker := filepath.Join(target, ".migrated")
	if _, err := os.Stat(marker); err == nil {
		return
	}
	if _, err := os.Stat(legacy); err != nil {
		os.WriteFile(marker, []byte("nessuna cartella precedente\n"), 0600)
		return
	}
	if _, err := os.Stat(filepath.Join(target, "config.json")); err != nil {
		for _, name := range []string{"config.json", "settings", "certificates", "workspace.json", "formulas.json", "recordings_location.json", "acquisition_events.sqlite3"} {
			if err := copyTree(filepath.Join(legacy, name), filepath.Join(target, name)); err != nil && !os.IsNotExist(err) {
				fmt.Fprintf(log, "Migrazione di %s non riuscita: %v\n", name, err)
			}
		}
	}
	imports := filepath.Join(legacy, "recording_imports")
	if err := copyTree(imports, filepath.Join(target, "recording_imports")); err == nil {
		os.RemoveAll(imports)
	} else if !os.IsNotExist(err) {
		fmt.Fprintf(log, "Migrazione dei file importati non riuscita: %v\n", err)
	}
	for _, cache := range []string{"live_analysis_cache", "calculation_cache", "runtime"} {
		os.RemoveAll(filepath.Join(legacy, cache))
	}
	os.WriteFile(marker, []byte("dati migrati da "+legacy+"\n"), 0600)
	fmt.Fprintf(log, "Dati migrati da %s a %s; cache precedenti rimosse\n", legacy, target)
}

// copyTree copies a file or a folder; existing destination files are kept.
func copyTree(source, target string) error {
	info, err := os.Stat(source)
	if err != nil {
		return err
	}
	if !info.IsDir() {
		if _, err := os.Stat(target); err == nil {
			return nil
		}
		in, err := os.Open(source)
		if err != nil {
			return err
		}
		defer in.Close()
		if err = os.MkdirAll(filepath.Dir(target), 0700); err != nil {
			return err
		}
		out, err := os.OpenFile(target, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			return err
		}
		if _, err = io.Copy(out, in); err != nil {
			out.Close()
			os.Remove(target)
			return err
		}
		return out.Close()
	}
	entries, err := os.ReadDir(source)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if err := copyTree(filepath.Join(source, entry.Name()), filepath.Join(target, entry.Name())); err != nil {
			return err
		}
	}
	return nil
}

// runtimeDirectory extracts the payload once per build into the data folder and reuses it
// on later starts (a complete marker guards against interrupted extractions).
func runtimeDirectory(dataDir string, progress func(float64)) (string, error) {
	digest := strings.TrimSpace(payloadDigest)
	if len(digest) < 16 {
		return "", fmt.Errorf("impronta del runtime mancante")
	}
	base := filepath.Join(dataDir, "runtime")
	target := filepath.Join(base, "v"+version+"-"+digest[:16])
	if _, err := os.Stat(filepath.Join(target, ".complete")); err == nil {
		progress(1)
		return target, nil
	}
	if err := os.MkdirAll(base, 0700); err != nil {
		return "", err
	}
	staging, err := os.MkdirTemp(base, "extract-")
	if err != nil {
		return "", err
	}
	if err = extract(staging, progress); err != nil {
		os.RemoveAll(staging)
		return "", err
	}
	if err = os.WriteFile(filepath.Join(staging, ".complete"), []byte(digest+"\n"), 0600); err != nil {
		os.RemoveAll(staging)
		return "", err
	}
	if err = os.Rename(staging, target); err != nil {
		os.RemoveAll(staging)
		// Another launch completed the same runtime first.
		if _, statErr := os.Stat(filepath.Join(target, ".complete")); statErr == nil {
			return target, nil
		}
		return "", err
	}
	// Older runtimes are removed when no running service holds their files.
	if entries, readErr := os.ReadDir(base); readErr == nil {
		for _, entry := range entries {
			if entry.IsDir() && entry.Name() != filepath.Base(target) && !strings.HasPrefix(entry.Name(), "extract-") {
				os.RemoveAll(filepath.Join(base, entry.Name()))
			}
		}
	}
	return target, nil
}

// relay copies the service output to the log and drives the splash from the markers.
func relay(output io.Reader, log io.Writer, splash *Splash, ready func()) {
	reader := bufio.NewReader(output)
	for {
		line, err := reader.ReadString('\n')
		if line != "" {
			io.WriteString(log, line)
			if rest, ok := strings.CutPrefix(strings.TrimSpace(line), strings.TrimSpace(startupMarker)); ok {
				field, _, _ := strings.Cut(strings.TrimSpace(rest), " ")
				if percent, convErr := strconv.Atoi(field); convErr == nil {
					splash.SetProgress(0.45 + 0.55*float64(percent)/100)
					if percent >= 100 {
						ready()
					}
				}
			}
		}
		if err != nil {
			return
		}
	}
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
	dataDir, legacyDir, err := dataDirectory(appDir)
	if err != nil {
		return err
	}
	logPath := filepath.Join(dataDir, "launcher-v"+version+".log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer logFile.Close()
	fmt.Fprintf(logFile, "\nAvvio V%s; applicativo: %s; dati: %s\n", version, appDir, dataDir)
	if legacyDir != "" {
		migrate(legacyDir, dataDir, logFile)
	}
	var splash *Splash
	tool := len(os.Args) > 1 && os.Args[1] == "--tool"
	if os.Getenv("SPACE_NO_SPLASH") == "" && !tool {
		splash = StartSplash()
	}
	closeSplash := func() { go func() { time.Sleep(600 * time.Millisecond); splash.Close() }() }
	defer splash.Close()
	splash.SetProgress(0.05)
	runtimeDir, err := runtimeDirectory(dataDir, func(f float64) { splash.SetProgress(0.05 + 0.40*f) })
	if err != nil {
		return fmt.Errorf("estrazione dei servizi: %w", err)
	}
	service := filepath.Join(runtimeDir, "SpaceAnalyzer.exe")
	if tool {
		// Offline tools (recover/inspect/export) run in the console of the caller.
		command := exec.Command(service, os.Args[1:]...)
		command.Env = environment(appDir, runtimeDir, dataDir)
		command.Stdout, command.Stderr, command.Stdin = os.Stdout, os.Stderr, os.Stdin
		return command.Run()
	}
	command := exec.Command(service)
	command.Dir = runtimeDir
	command.Env = environment(appDir, runtimeDir, dataDir)
	command.Stderr = logFile
	output, err := command.StdoutPipe()
	if err != nil {
		return err
	}
	command.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000}
	// The service binds HTTP before opening the default browser and owns the instance lock.
	started := time.Now()
	if err = command.Start(); err != nil {
		return fmt.Errorf("avvio dei servizi non riuscito: %w\nDettagli: %s", err, logPath)
	}
	splash.SetProgress(0.45)
	// Never leave the splash on screen if a stage marker is missing.
	timeout := time.AfterFunc(3*time.Minute, func() { splash.Close() })
	relayed := make(chan struct{})
	go func() {
		relay(output, logFile, splash, closeSplash)
		close(relayed)
	}()
	<-relayed
	timeout.Stop()
	if err = command.Wait(); err != nil {
		if crash := latestCrash(dataDir, started); crash != "" {
			return fmt.Errorf("esecuzione dei servizi non riuscita: %w\nErrore registrato: %s\nDettagli: %s", err, crash, logPath)
		}
		return fmt.Errorf("esecuzione dei servizi non riuscita: %w\nDettagli: %s", err, logPath)
	}
	fmt.Fprintln(logFile, "Servizi terminati.")
	return nil
}

// latestCrash returns the crash report the service wrote during this run, if any.
func latestCrash(dataDir string, since time.Time) string {
	matches, _ := filepath.Glob(filepath.Join(dataDir, "crash-*.txt"))
	newest, newestTime := "", since.Add(-2*time.Second)
	for _, path := range matches {
		if info, err := os.Stat(path); err == nil && info.ModTime().After(newestTime) {
			newest, newestTime = path, info.ModTime()
		}
	}
	return newest
}

func main() {
	if err := run(); err != nil {
		message(err.Error())
		os.Exit(1)
	}
}
