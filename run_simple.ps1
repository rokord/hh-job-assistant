$ErrorActionPreference = "Stop"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Set-Location $PSScriptRoot

function Write-ScriptRunLog {
    param (
        [string]$ScriptName,
        [string]$ScriptPath
    )

    $dataDir = Join-Path $PSScriptRoot "data"

    if (!(Test-Path $dataDir)) {
        New-Item -ItemType Directory -Path $dataDir | Out-Null
    }

    $logPath = Join-Path $dataDir "script_runs.jsonl"

    $payload = [ordered]@{
        created_at = (Get-Date).ToString("s")
        script_name = $ScriptName
        script_path = $ScriptPath
    }

    $json = $payload | ConvertTo-Json -Compress

    Add-Content -Path $logPath -Value $json -Encoding UTF8
}

function Run-PythonScript {
    param (
        [string]$ScriptName,
        [string]$ScriptPath
    )

    Write-ScriptRunLog -ScriptName $ScriptName -ScriptPath $ScriptPath

    if (Test-Path ".\.venv\Scripts\python.exe") {
        .\.venv\Scripts\python.exe $ScriptPath
    } else {
        python $ScriptPath
    }
}

while ($true) {
    Clear-Host

    Write-Host ""
    Write-Host "HHH HeadHunter Hacker приветствует тебя!" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "1 - Собрать вакансии в JSON"
    Write-Host "2 - Полуавтоматические отклики из списка вручную"
    Write-Host "3 - Автоотклики по страницам"
    Write-Host "4 - Напоминания в чатах HH"
    Write-Host "5 - Открыть локальный dashboard"
    Write-Host "6 - Открыть настройки"
    Write-Host "7 - Открыть сопроводительное письмо"
    Write-Host "8 - Открыть blacklist компаний"
    Write-Host "0 - Выход"
    Write-Host ""

    $choice = Read-Host "Выбери пункт"

    switch ($choice) {
        "1" {
            Write-Host "Запускаю сбор вакансий..." -ForegroundColor Green
            Run-PythonScript -ScriptName "Сбор вакансий" -ScriptPath "src\main.py"
            Pause
        }

        "2" {
            Write-Host "Запускаю полуавтоматические отклики..." -ForegroundColor Green
            Run-PythonScript -ScriptName "Полуавто отклики" -ScriptPath "src\apply_from_list.py"
            Pause
        }

        "3" {
            Write-Host "Запускаю автоотклики по страницам..." -ForegroundColor Green
            Run-PythonScript -ScriptName "Автоотклики" -ScriptPath "src\auto_apply_pages.py"
            Pause
        }

        "4" {
            Write-Host "Запускаю напоминания в чатах HH..." -ForegroundColor Green
            Run-PythonScript -ScriptName "Напоминания в чатах" -ScriptPath "src\followup_chats_review.py"
            Pause
        }

        "5" {
            Write-Host "Открываю локальный dashboard..." -ForegroundColor Green
            Run-PythonScript -ScriptName "Dashboard" -ScriptPath "src\dashboard.py"
            Pause
        }

        "6" {
            notepad "config\settings.json"
        }

        "7" {
            notepad "config\cover_letter.txt"
        }

        "8" {
            notepad "config\blacklist_companies.txt"
        }

        "0" {
            break
        }

        default {
            Write-Host "Неизвестный пункт меню" -ForegroundColor Red
            Start-Sleep -Seconds 1
        }
    }
}