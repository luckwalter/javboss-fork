package util

import (
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"

	"github.com/h2non/filetype"

	"javboss/internal/runtimeconfig"
)

type VideoMetadata struct {
	Codec           string
	VideoCodec      string
	AudioCodec      string
	Container       string
	FormatName      string
	Width           int
	Height          int
	FPS             float64
	SampleRate      int
	Channels        int
	DurationSeconds float64
	FormatBitRate   int64
	VideoBitRate    int64
	AudioBitRate    int64
}

func (m *VideoMetadata) Fingerprint(size int64) string {
	fps := m.FPS
	if fps > 0 {
		fps = math.Round(fps*1000) / 1000
	}
	dur := math.Round(m.DurationSeconds)
	return fmt.Sprintf("%dx%d|%s|%.3f|%d|%d|%.0f|%d",
		m.Width,
		m.Height,
		strings.TrimSpace(m.Codec),
		fps,
		m.SampleRate,
		m.Channels,
		dur,
		size)
}

// FingerprintV2 returns a metadata-only fingerprint with higher granularity.
// Format: widthxheight|bitrate|video_bitrate|audio_bitrate|duration_ms|size
func (m *VideoMetadata) FingerprintV2(size int64) string {
	durationMs := int64(math.Round(m.DurationSeconds * 1000))
	return fmt.Sprintf("%dx%d|%d|%d|%d|%d|%d",
		m.Width,
		m.Height,
		m.FormatBitRate,
		m.VideoBitRate,
		m.AudioBitRate,
		durationMs,
		size)
}

// IsVideoCandidate reports whether a file should be passed to ffprobe for final
// video-stream validation. Known video extensions are accepted as candidates so
// uncommon or newer container signatures are not filtered out prematurely.
func IsVideoCandidate(path string) bool {
	if hasVideoExtension(filepath.Ext(path)) {
		return true
	}
	return IsVideo(path)
}

// IsVideo detects video content by inspecting the initial bytes and matching
// known container signatures.
func IsVideo(path string) bool {
	ext := strings.ToLower(filepath.Ext(path))
	f, err := os.Open(path)
	if err != nil {
		return false
	}
	defer f.Close()

	// filetype recommends at least 261 bytes. Read enough MPEG-TS packets to
	// recognize transport streams by content even when the extension is incorrect
	// (for example, a .mp4 file containing MPEG-TS data).
	header := make([]byte, 4*204)
	n, err := f.Read(header)
	if n == 0 && err != nil {
		return false
	}
	buf := header[:n]
	if isMPEGTransportStreamHeader(buf) {
		return true
	}
	if hasVideoExtension(ext) && isISOBMFFHeader(buf) {
		return true
	}
	if isRealMediaExtension(ext) && isRealMediaHeader(buf) {
		return true
	}
	kind, err := filetype.Match(buf)
	if err != nil {
		return false
	}
	if kind == filetype.Unknown {
		return false
	}
	// Accept any MIME with top-level type "video"
	return strings.HasPrefix(kind.MIME.Value, "video/") || kind.MIME.Type == "video"
}

func isMPEGTransportStreamHeader(buf []byte) bool {
	const packetsToCheck = 4
	layouts := []struct {
		packetSize int
		syncOffset int
	}{
		{packetSize: 188, syncOffset: 0},
		{packetSize: 192, syncOffset: 4},
		{packetSize: 192, syncOffset: 0},
		{packetSize: 204, syncOffset: 0},
	}
	for _, layout := range layouts {
		required := layout.syncOffset + (packetsToCheck-1)*layout.packetSize + 1
		if len(buf) < required {
			continue
		}
		matched := true
		for packet := 0; packet < packetsToCheck; packet++ {
			if buf[layout.syncOffset+packet*layout.packetSize] != 0x47 {
				matched = false
				break
			}
		}
		if matched {
			return true
		}
	}
	return false
}

func isISOBMFFHeader(buf []byte) bool {
	if len(buf) < 12 || !bytes.Equal(buf[4:8], []byte("ftyp")) {
		return false
	}
	boxSize := binary.BigEndian.Uint32(buf[:4])
	return boxSize == 1 || boxSize >= 16
}

func hasVideoExtension(ext string) bool {
	switch strings.ToLower(strings.TrimSpace(ext)) {
	case ".3g2", ".3gp",
		".av1",
		".asf", ".avi",
		".divx",
		".dv",
		".f4v", ".flv",
		".264", ".265", ".h264", ".h265", ".hevc",
		".ivf",
		".m2ts", ".m2v", ".m4v", ".mkv", ".mov", ".mp4", ".mpe", ".mpeg", ".mpegts", ".mpg", ".mpv", ".mts", ".mxf",
		".nut",
		".ogg", ".ogm", ".ogv",
		".qt",
		".rm", ".rmvb",
		".ts",
		".vob",
		".webm", ".wmv",
		".xvid",
		".y4m", ".yuv":
		return true
	default:
		return false
	}
}

func isRealMediaExtension(ext string) bool {
	switch ext {
	case ".rmvb", ".rm":
		return true
	default:
		return false
	}
}

func isRealMediaHeader(buf []byte) bool {
	return bytes.HasPrefix(buf, []byte(".RMF"))
}

// ErrFFToolMissing 标识「容器内缺少 ffmpeg/ffprobe 可执行文件」。
// 这类错误的底层链同样满足 errors.Is(err, fs.ErrNotExist)（stat 失败），
// 若不单独识别，上层会把「工具缺失」误报成「视频文件或所在目录不存在」。
var ErrFFToolMissing = errors.New("ff tool missing")

var (
	ffprobeMu       sync.Mutex
	ffprobePath     string
	ffprobeResolved bool
)

// ContainerFFBinaryDir is the primary (current image) location for FFmpeg tools in Docker.
const ContainerFFBinaryDir = "/app/internal/bin"

// containerFFBinaryFallbackDirs 兼容历史镜像布局：v2.1.0 的 Dockerfile 把
// ffmpeg/ffprobe 放在 /usr/local/bin，v2.1.1 起改为 /app/internal/bin。
var containerFFBinaryFallbackDirs = []string{
	"/usr/local/bin",
}

// ResolveFFprobePath resolves the ffprobe binary location.
// 只在解析成功时缓存：失败结果不再被永久记住，工具补齐后无需重启进程即可恢复。
func ResolveFFprobePath() (string, error) {
	ffprobeMu.Lock()
	defer ffprobeMu.Unlock()
	if ffprobeResolved {
		return ffprobePath, nil
	}
	path, err := findFFprobePath()
	if err != nil {
		return "", err
	}
	ffprobePath, ffprobeResolved = path, true
	return path, nil
}

// ResetFFprobePathCache 使下一次 ResolveFFprobePath 重新探测。
// 供「安装/下载 FFmpeg 工具之后」主动失效缓存。
func ResetFFprobePathCache() {
	ffprobeMu.Lock()
	ffprobePath, ffprobeResolved = "", false
	ffprobeMu.Unlock()
}

// IsContainerFFBinaryPath 判断给定路径是否位于容器内的 FFmpeg 工具目录
// （当前布局 /app/internal/bin，或历史布局 /usr/local/bin）。
// 用于区分「镜像内置的 FFmpeg」与「系统安装的 FFmpeg」。
func IsContainerFFBinaryPath(path string) bool {
	if strings.TrimSpace(path) == "" {
		return false
	}
	dir := filepath.Dir(filepath.Clean(path))
	if dir == ContainerFFBinaryDir {
		return true
	}
	for _, fallback := range containerFFBinaryFallbackDirs {
		if dir == fallback {
			return true
		}
	}
	return false
}

// ResolveFFmpegPath resolves the ffmpeg binary location.
// 容器模式按候选链回退（env → /app/internal/bin → /usr/local/bin → PATH，见
// findFFBinaryPathWithLookup）；原生安装使用工具下载，macOS 另支持随包可执行文件；
// release 构建仅相对可执行文件目录解析。
func ResolveFFmpegPath() (string, error) {
	return findFFmpegPath()
}

func findFFprobePath() (string, error) {
	return findFFBinaryPath("ffprobe")
}

func findFFmpegPath() (string, error) {
	return findFFBinaryPath("ffmpeg")
}

// FFmpegToolRelativePath returns the persistent project-relative path used for
// FFmpeg downloaded from the frontend tools panel.
func FFmpegToolRelativePath() string {
	platformOS := runtime.GOOS
	if platformOS == "darwin" {
		platformOS = "macos"
	}
	platformArch := runtime.GOARCH
	if platformArch == "amd64" {
		platformArch = "x86_64"
	}

	binName := "ffmpeg"
	if runtime.GOOS == "windows" {
		binName += ".exe"
	}
	return filepath.Join("data", "tools", platformOS+"-"+platformArch, binName)
}

func findFFBinaryPath(name string) (string, error) {
	return findFFBinaryPathWithLookup(name, exec.LookPath)
}

func findFFBinaryPathWithLookup(name string, lookup func(string) (string, error)) (string, error) {
	if runtimeconfig.ContainerMode() {
		// 容器内按优先级回退，而不是只认一个硬编码路径。
		// 背景：官方镜像布局随版本变过（v2.1.0 → /usr/local/bin，v2.1.1 起 → /app/internal/bin），
		// 单点硬编码会让「二进制与镜像底座版本不一致」直接导致播放整体失效，
		// 且错误信息把人误导向「文件不存在」。
		candidates := make([]string, 0, 2+len(containerFFBinaryFallbackDirs))
		// 1) 显式环境变量优先（FFPROBE_PATH / FFMPEG_PATH）
		if envPath := strings.TrimSpace(os.Getenv(strings.ToUpper(name) + "_PATH")); envPath != "" {
			candidates = append(candidates, envPath)
		}
		// 2) 当前镜像布局 → 3) 历史镜像布局 → 4) PATH 查找
		candidates = append(candidates, ContainerFFBinaryDir+"/"+name)
		for _, dir := range containerFFBinaryFallbackDirs {
			candidates = append(candidates, dir+"/"+name)
		}
		candidates = append(candidates, name)

		tried := make([]string, 0, len(candidates))
		for _, candidate := range candidates {
			resolved, err := lookup(candidate)
			if err == nil {
				return resolved, nil
			}
			tried = append(tried, candidate)
		}
		return "", fmt.Errorf("%w: %s not found in container (tried: %s)",
			ErrFFToolMissing, name, strings.Join(tried, ", "))
	}

	var candidates []string

	binName := name
	if runtime.GOOS == "windows" {
		binName = name + ".exe"
	}

	releaseMode := os.Getenv("JAVBOSS_BUILD_MODE") == "release"
	if !releaseMode {
		if wd, err := os.Getwd(); err == nil {
			candidates = append(
				candidates,
				ffBinaryCandidatesForBase(wd, name, binName, runtime.GOOS, FFmpegToolRelativePath())...,
			)
		}
	}
	if execPath, err := os.Executable(); err == nil {
		execDir := filepath.Dir(execPath)
		candidates = append(
			candidates,
			ffBinaryCandidatesForBase(execDir, name, binName, runtime.GOOS, FFmpegToolRelativePath())...,
		)
	} else if releaseMode {
		return "", fmt.Errorf("resolve executable directory for %s: %w", name, err)
	}
	for _, candidate := range candidates {
		if candidate == "" {
			continue
		}
		if resolved, err := lookup(candidate); err == nil {
			return resolved, nil
		}
	}
	if name == "ffmpeg" {
		if runtime.GOOS != "darwin" {
			return "", fmt.Errorf("%s not found; download it from Tools to %s", name, filepath.ToSlash(FFmpegToolRelativePath()))
		}
		return "", fmt.Errorf("%s not found; use the release bundle at internal/bin/%s or download it from Tools to %s", name, binName, filepath.ToSlash(FFmpegToolRelativePath()))
	}
	return "", fmt.Errorf("%s not found; place binary at internal/bin/%s", name, binName)
}

func ffBinaryCandidatesForBase(baseDir string, name string, binName string, goos string, ffmpegToolPath string) []string {
	bundledPath := filepath.Join(baseDir, "internal", "bin", binName)
	if name != "ffmpeg" {
		return []string{bundledPath}
	}

	downloadedPath := filepath.Join(baseDir, ffmpegToolPath)
	if goos == "darwin" {
		return []string{bundledPath, downloadedPath}
	}
	return []string{downloadedPath}
}

// ProbeVideo extracts codec/resolution/fps/duration using ffprobe.
func ProbeVideo(path string) (*VideoMetadata, error) {
	return ProbeVideoContext(context.Background(), path)
}

// ProbeVideoContext extracts codec/resolution/fps/duration using ffprobe.
func ProbeVideoContext(ctx context.Context, path string) (*VideoMetadata, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	if strings.TrimSpace(path) == "" {
		return nil, errors.New("empty path")
	}
	ffprobe, err := ResolveFFprobePath()
	if err != nil {
		return nil, err
	}
	// -v quiet -print_format json -show_streams -select_streams v:0
	cmd := BackgroundCommandContext(ctx, ffprobe,
		"-v", "error",
		"-print_format", "json",
		"-show_entries", "stream=index,codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate,sample_rate,channels,bit_rate",
		"-show_entries", "format=duration,size,bit_rate,format_name",
		path,
	)
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		if ctxErr := ctx.Err(); ctxErr != nil {
			return nil, ctxErr
		}
		errMsg := strings.TrimSpace(stderr.String())
		if errMsg != "" {
			return nil, fmt.Errorf("ffprobe: %w: %s", err, errMsg)
		}
		return nil, fmt.Errorf("ffprobe: %w", err)
	}
	meta, err := parseFFprobeOutput(out, path)
	if err != nil {
		return nil, err
	}
	return meta, nil
}

type ffprobeStream struct {
	CodecName    string `json:"codec_name"`
	CodecType    string `json:"codec_type"`
	PixFmt       string `json:"pix_fmt"`
	Width        int    `json:"width"`
	Height       int    `json:"height"`
	AvgFrameRate string `json:"avg_frame_rate"`
	RFrameRate   string `json:"r_frame_rate"`
	Duration     string `json:"duration"`
	DurationTS   int64  `json:"duration_ts"`
	SampleRate   string `json:"sample_rate"`
	Channels     int    `json:"channels"`
	BitRate      string `json:"bit_rate"`
}
type ffprobeResult struct {
	Streams []ffprobeStream `json:"streams"`
	Format  struct {
		Duration   string `json:"duration"`
		Size       string `json:"size"`
		BitRate    string `json:"bit_rate"`
		FormatName string `json:"format_name"`
	} `json:"format"`
}

func parseFFprobeOutput(out []byte, path string) (*VideoMetadata, error) {
	var res ffprobeResult
	if err := json.Unmarshal(out, &res); err != nil {
		return nil, fmt.Errorf("parse ffprobe json: %w", err)
	}
	var video *ffprobeStream
	var audio *ffprobeStream
	for i := range res.Streams {
		s := res.Streams[i]
		switch strings.ToLower(strings.TrimSpace(s.CodecType)) {
		case "video":
			if video == nil {
				video = &s
			}
		case "audio":
			if audio == nil {
				audio = &s
			}
		}
	}
	if video == nil {
		return nil, errors.New("ffprobe: no video stream")
	}
	fps := parseRate(video.AvgFrameRate)
	if fps == 0 {
		fps = parseRate(video.RFrameRate)
	}
	duration := parseDurationSeconds(video.Duration, video.DurationTS, fps)
	if duration == 0 {
		duration = parseFloat(res.Format.Duration)
	}
	meta := &VideoMetadata{
		Codec:           strings.TrimSpace(video.CodecName),
		VideoCodec:      strings.TrimSpace(video.CodecName),
		FormatName:      normalizeFormatName(res.Format.FormatName),
		Container:       detectContainer(res.Format.FormatName, path),
		Width:           video.Width,
		Height:          video.Height,
		FPS:             fps,
		DurationSeconds: duration,
	}
	if audio != nil {
		meta.AudioCodec = strings.TrimSpace(audio.CodecName)
		if sr, err := strconv.Atoi(strings.TrimSpace(audio.SampleRate)); err == nil {
			meta.SampleRate = sr
		}
		meta.Channels = audio.Channels
		if meta.DurationSeconds == 0 {
			meta.DurationSeconds = parseDurationSeconds(audio.Duration, audio.DurationTS, 0)
		}
	}
	meta.FormatBitRate = parseInt64(res.Format.BitRate)
	meta.VideoBitRate = parseInt64(video.BitRate)
	if audio != nil {
		meta.AudioBitRate = parseInt64(audio.BitRate)
	}
	return meta, nil
}

func parseRate(rate string) float64 {
	rate = strings.TrimSpace(rate)
	if rate == "" || rate == "0/0" {
		return 0
	}
	if strings.Contains(rate, "/") {
		parts := strings.Split(rate, "/")
		if len(parts) == 2 {
			num, _ := strconv.ParseFloat(parts[0], 64)
			den, _ := strconv.ParseFloat(parts[1], 64)
			if num > 0 && den > 0 {
				return num / den
			}
		}
	}
	v, _ := strconv.ParseFloat(rate, 64)
	return v
}

func parseDurationSeconds(durationStr string, durationTS int64, fps float64) float64 {
	if durationStr != "" {
		if v, err := strconv.ParseFloat(durationStr, 64); err == nil && v > 0 {
			return v
		}
	}
	if durationTS > 0 && fps > 0 {
		return float64(durationTS) / fps
	}
	return 0
}

func parseFloat(raw string) float64 {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return 0
	}
	v, _ := strconv.ParseFloat(raw, 64)
	return v
}

func parseInt64(raw string) int64 {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return 0
	}
	v, _ := strconv.ParseInt(raw, 10, 64)
	return v
}

func normalizeFormatName(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	part := strings.Split(raw, ",")[0]
	return strings.ToLower(strings.TrimSpace(part))
}

func detectContainer(formatName, path string) string {
	switch strings.ToLower(strings.TrimSpace(filepath.Ext(path))) {
	case ".mp4", ".m4v":
		return "mp4"
	case ".mov":
		return "mov"
	case ".webm":
		return "webm"
	case ".mkv":
		return "mkv"
	case ".avi":
		return "avi"
	case ".wmv":
		return "wmv"
	case ".flv":
		return "flv"
	case ".rmvb", ".rm":
		return "rmvb"
	case ".ts":
		return "ts"
	case ".m2ts", ".mts":
		return "m2ts"
	case ".mpg", ".mpeg":
		return "mpeg"
	}
	return normalizeFormatName(formatName)
}
