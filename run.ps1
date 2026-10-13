$ErrorActionPreference = "Stop"

$ProjectDir = "C:\Users\notamedia\Documents\hh-job-assistant-clean"

Set-Location $ProjectDir

Write-Host ""
Write-Host "HH Job Assistant" -ForegroundColor Cyan
Write-Host "================"
Write-Host ""
Write-Host "Project folder: $ProjectDir"
Write-Host ""

if (-not (Test-Path ".venv")) {
    Write-Host "Virtual environment not found. Creating .venv..." -ForegroundColor Yellow
    python -m venv .venv
}

$ActivatePath = ".\.venv\Scripts\Activate.ps1"

if (-not (Test-Path $ActivatePath)) {
    Write-Host "Virtual environment activation file not found:" -ForegroundColor Red
    Write-Host $ActivatePath
    exit 1
}

. $ActivatePath

Write-Host "Virtual environment activated." -ForegroundColor Green
Write-Host ""

if (Test-Path "requirements.txt") {
    Write-Host "Installing/checking dependencies..." -ForegroundColor Yellow
    python -m pip install -r requirements.txt
    Write-Host "Dependencies are ready." -ForegroundColor Green
    Write-Host ""
}

if (-not (Test-Path "config\settings.json") -and (Test-Path "config\settings.example.json")) {
    Write-Host "Creating config\settings.json from example..." -ForegroundColor Yellow
    Copy-Item "config\settings.example.json" "config\settings.json"
}

if (-not (Test-Path "config\cover_letter.txt") -and (Test-Path "config\cover_letter.example.txt")) {
    Write-Host "Creating config\cover_letter.txt from example..." -ForegroundColor Yellow
    Copy-Item "config\cover_letter.example.txt" "config\cover_letter.txt"
}

if (-not (Test-Path "config\blacklist_companies.txt") -and (Test-Path "config\blacklist_companies.example.txt")) {
    Write-Host "Creating config\blacklist_companies.txt from example..." -ForegroundColor Yellow
    Copy-Item "config\blacklist_companies.example.txt" "config\blacklist_companies.txt"
}

Write-Host ""
Write-Host "Choose mode:" -ForegroundColor Cyan
Write-Host ""
Write-Host "1 - Collect vacancies to JSON"
Write-Host "2 - Semi-auto apply from list"
Write-Host "3 - Auto apply by pages"
Write-Host "4 - Open project in VSCode"
Write-Host "5 - Open settings"
Write-Host "6 - Open cover letter"
Write-Host "7 - Open company blacklist"
Write-Host "8 - Git status"
Write-Host "9 - Git commit and push"
Write-Host "10 - HH follow-up messages"
Write-Host "0 - Exit"
Write-Host ""

$Choice = Read-Host "Enter number"

Write-Host ""

switch ($Choice) {
    "1" {
        Write-Host "Running vacancy collection..." -ForegroundColor Green
        python src\main.py
    }
    "2" {
        Write-Host "Running semi-auto apply..." -ForegroundColor Green
        python src\apply_from_list.py
    }
    "3" {
        Write-Host "Running auto apply by pages..." -ForegroundColor Green
        python src\auto_apply_pages.py
    }
    "4" {
        Write-Host "Opening VSCode..." -ForegroundColor Green
        code .
    }
    "5" {
        Write-Host "Opening config\settings.json..." -ForegroundColor Green
        code config\settings.json
    }
    "6" {
        Write-Host "Opening config\cover_letter.txt..." -ForegroundColor Green
        code config\cover_letter.txt
    }
    "7" {
        Write-Host "Opening config\blacklist_companies.txt..." -ForegroundColor Green
        code config\blacklist_companies.txt
    }
    "8" {
        Write-Host "Git status:" -ForegroundColor Green
        git status
    }
    "9" {
        Write-Host "Git status:" -ForegroundColor Green
        git status

        $CommitMessage = Read-Host "Commit message"

        if ([string]::IsNullOrWhiteSpace($CommitMessage)) {
            $CommitMessage = "Update project"
        }

        git add .
        git commit -m $CommitMessage
        git push
    }
    "10" {
    Write-Host "Running HH chat follow-up messages..." -ForegroundColor Green
    python src\followup_chats_review.py
    }
    "0" {
        Write-Host "Exit."
        exit 0
    }
    default {
        Write-Host "Unknown menu item." -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "Done." -ForegroundColor Cyan
Read-Host "Press Enter to close"