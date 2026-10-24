package main

import (
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
)

// Framing is bounded JSON followed by channel-major, little-endian Float32 blocks.
// Only this private helper interprets it. It is not an iba DAT implementation.
type Channel struct {
	Name   string  `json:"name"`
	Unit   string  `json:"unit"`
	NodeID string  `json:"node_id"`
	Count  int     `json:"count"`
	Period float32 `json:"period"`
	Offset float32 `json:"offset"`
}
type Header struct {
	Channels []Channel         `json:"channels"`
	Info     map[string]string `json:"info"`
}
type ExportBackend interface {
	Backend
	WriteDataset(string, Header, io.Reader) error
	ReadChannel(string, string, int) (Dataset, error)
}
type ExportResult struct {
	OK        bool   `json:"ok"`
	Native    bool   `json:"native"`
	Simulated bool   `json:"simulated"`
	Licensed  bool   `json:"licensed"`
	Version   string `json:"version,omitempty"`
	Error     string `json:"error,omitempty"`
	Channels  int    `json:"channels"`
	Samples   int    `json:"samples"`
	Roundtrip bool   `json:"roundtrip_verified"`
}

func readHeader(reader io.Reader) (h Header, err error) {
	var size uint32
	if err = binary.Read(reader, binary.LittleEndian, &size); err != nil {
		return
	}
	if size == 0 || size > 1024*1024 {
		return h, errors.New("Intestazione troppo grande o vuota")
	}
	raw := make([]byte, size)
	if _, err = io.ReadFull(reader, raw); err != nil {
		return
	}
	if err = json.Unmarshal(raw, &h); err != nil {
		return
	}
	if len(h.Channels) == 0 || len(h.Channels) > 192 {
		return h, errors.New("Selezionare da 1 a 64 variabili (3 canali DAT per variabile)")
	}
	names := map[string]bool{}
	total := 0
	for _, c := range h.Channels {
		if c.Name == "" || len(c.Name) > 512 || names[c.Name] || c.Count < 1 || c.Count > 2000000 || c.Period <= 0 || math.IsNaN(float64(c.Period)) || math.IsInf(float64(c.Period), 0) || math.IsNaN(float64(c.Offset)) || math.IsInf(float64(c.Offset), 0) {
			return h, errors.New("Definizione canale non valida")
		}
		names[c.Name] = true
		total += c.Count
	}
	if total > 24000000 {
		return h, errors.New("Troppi campioni per singolo file DAT")
	}
	return
}

func exportFile(dll, path string, input io.Reader, factory BackendFactory) (result ExportResult) {
	fail := func(err error) ExportResult { result.Error = err.Error(); return result }
	if _, err := verifyDLL(dll); err != nil {
		return fail(err)
	}
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		return fail(errors.New("Destinazione DAT gia esistente o non accessibile"))
	}
	h, err := readHeader(input)
	if err != nil {
		return fail(err)
	}
	backend, err := factory(dll)
	if err != nil {
		return fail(err)
	}
	defer backend.Release()
	result.Version, err = backend.Version()
	if err != nil {
		return fail(err)
	}
	result.Licensed, err = backend.Licensed()
	if err != nil {
		return fail(err)
	}
	if !result.Licensed {
		return fail(errors.New("Licensed = false: licenza di scrittura iba non disponibile"))
	}
	writer, ok := backend.(ExportBackend)
	if !ok {
		return fail(errors.New("Backend privo di supporto dataset"))
	}
	// A seekable input lets read-back compare each original Float32 without copying the job into RAM.
	stream, ok := input.(io.ReadSeeker)
	if !ok {
		return fail(errors.New("Input deve essere un file seekable"))
	}
	position, err := stream.Seek(0, io.SeekCurrent)
	if err != nil {
		return fail(err)
	}
	if err = writer.WriteDataset(path, h, input); err != nil {
		return fail(err)
	}
	if _, err = stream.Seek(position, io.SeekStart); err != nil {
		return fail(err)
	}
	for _, c := range h.Channels {
		actual, e := writer.ReadChannel(path, c.Name, c.Count)
		if e != nil {
			return fail(e)
		}
		if len(actual.Values) != c.Count || math.Float32bits(actual.Timebase) != math.Float32bits(c.Period) || math.Float32bits(actual.XOffset) != math.Float32bits(c.Offset) {
			return fail(fmt.Errorf("Verifica campioni/tempo fallita: %s", c.Name))
		}
		var bits uint32
		for _, v := range actual.Values {
			if e = binary.Read(stream, binary.LittleEndian, &bits); e != nil {
				return fail(e)
			}
			if math.Float32bits(v) != bits {
				return fail(fmt.Errorf("Verifica valore Float32 fallita: %s", c.Name))
			}
		}
		result.Samples += c.Count
	}
	var trailing [1]byte
	if n, e := stream.Read(trailing[:]); n != 0 || e != io.EOF {
		return fail(errors.New("Dati aggiuntivi inattesi nel job"))
	}
	result.Channels = len(h.Channels)
	result.Roundtrip = true
	result.OK = true
	return
}
