; Windows-native voice transcription adapter for AutoHotkey v2.
; This wrapper owns hotkey, recording, clipboard, paste, and startup registration.
; It delegates transcription orchestration to the shared Python core.

#Requires AutoHotkey v2.0
#SingleInstance Force

global PythonExe := "python.exe"
global RepoRoot := "C:\CENTRAL"
global ConfigPath := RepoRoot . "\tools\voice_ptt\config.toml"
global CoreModule := "tools.voice_ptt_v2"
global FfmpegPath := "ffmpeg.exe"
global AudioInput := "audio=Microphone"
global TempDir := EnvGet("TEMP") . "\voice-ptt"
global ResultPath := TempDir . "\last-result.json"
global TranscriptPath := TempDir . "\last-transcript.txt"
global ToggleHotkey := "^+r"
global PasteEnabled := true
global PasteDelayMs := 100
global StartBeepHz := 880
global StopBeepHz := 660
global BeepDurationMs := 140

global isRecording := false
global recordingExec := ""
global audioPath := ""

DirCreate(TempDir)
Hotkey(ToggleHotkey, ToggleRecording)

RequireFile(path, label) {
    if !FileExist(path) {
        throw Error(label . " not found: " . path)
    }
}

ResolveExecutable(path, label) {
    if FileExist(path) {
        return path
    }
    split := StrSplit(EnvGet("PATH"), ";")
    for dir in split {
        candidate := RTrim(dir, "\/") . "\" . path
        if FileExist(candidate) {
            return candidate
        }
    }
    throw Error(label . " not found: " . path)
}

ValidateSetup() {
    global PythonExe, RepoRoot, ConfigPath, FfmpegPath
    RequireFile(RepoRoot, "Repo root")
    RequireFile(ConfigPath, "Config file")
    PythonExe := ResolveExecutable(PythonExe, "Python executable")
    FfmpegPath := ResolveExecutable(FfmpegPath, "ffmpeg executable")
}

ToggleRecording(*) {
    global isRecording
    if isRecording {
        StopRecordingAndTranscribe()
    } else {
        StartRecording()
    }
}

StartRecording() {
    global isRecording, recordingExec, audioPath, TempDir, FfmpegPath, AudioInput
    try {
        ValidateSetup()
    } catch as err {
        TrayTip("Voice PTT", err.Message, 3)
        return
    }
    SoundBeep(StartBeepHz, BeepDurationMs)
    stamp := FormatTime(, "yyyyMMdd-HHmmss")
    audioPath := TempDir . "\capture-" . stamp . ".wav"
    command := Format(
        '"{1}" -hide_banner -loglevel error -y -f dshow -i "{2}" "{3}"',
        FfmpegPath,
        AudioInput,
        audioPath,
    )
    shell := ComObject("WScript.Shell")
    recordingExec := shell.Exec(command)
    isRecording := true
    TrayTip("Voice PTT", "Recording started", 1)
}

StopRecordingAndTranscribe() {
    global isRecording, recordingExec, audioPath, PythonExe, CoreModule, ConfigPath, ResultPath, TranscriptPath, RepoRoot, PasteEnabled, PasteDelayMs
    if !isRecording {
        return
    }
    try {
        recordingExec.StdIn.Write("q")
    } catch {
        ProcessClose(recordingExec.ProcessID)
    }
    while (recordingExec.Status = 0) {
        Sleep(50)
    }
    SoundBeep(StopBeepHz, BeepDurationMs)
    isRecording := false
    recordingExec := ""
    if !FileExist(audioPath) {
        TrayTip("Voice PTT", "No audio file was produced", 3)
        return
    }
    command := Format(
        '"{1}" -m {2} transcribe-file --config "{3}" --audio-file "{4}" --platform windows --result-file "{5}" --text-file "{6}"',
        PythonExe,
        CoreModule,
        ConfigPath,
        audioPath,
        ResultPath,
        TranscriptPath,
    )
    status := RunWait(command, RepoRoot, "Hide")
    if status != 0 {
        TrayTip("Voice PTT", "Transcription failed; inspect result JSON", 3)
        return
    }
    if !FileExist(TranscriptPath) {
        TrayTip("Voice PTT", "Transcription produced no text file", 3)
        return
    }
    A_Clipboard := FileRead(TranscriptPath, "UTF-8")
    if PasteEnabled {
        Sleep(PasteDelayMs)
        Send("^v")
    }
    TrayTip("Voice PTT", "Transcript copied to clipboard", 1)
}
