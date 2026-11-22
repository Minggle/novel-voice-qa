# 离线兜底 TTS:Windows 自带语音(中文默认 Huihui),断网或 edge-tts 不可用时用
# 用法:
#   powershell -NoProfile -File speak.ps1 -Text "要读的文本"
#   powershell -NoProfile -File speak.ps1 -File 答案.txt
#   powershell -NoProfile -File speak.ps1 -File 答案.txt -Out 保存.wav
param(
    [string]$Text = "",
    [string]$File = "",
    [string]$Out = ""
)
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$zh = $synth.GetInstalledVoices() |
    Where-Object { $_.VoiceInfo.Culture.Name -like "zh*" } |
    Select-Object -First 1
if ($zh) { $synth.SelectVoice($zh.VoiceInfo.Name) }

if ($File) { $Text = Get-Content -Raw -Encoding UTF8 $File }
if (-not $Text.Trim()) { Write-Error "没有要朗读的文本"; exit 1 }

if ($Out) {
    $synth.SetOutputToWaveFile($Out)
    $synth.Speak($Text)
    $synth.SetOutputToDefaultAudioDevice()
    Write-Host "已保存: $Out"
} else {
    $synth.Speak($Text)
}
