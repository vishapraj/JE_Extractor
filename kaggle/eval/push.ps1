# Sends the evaluation to your Kaggle account (see README, "Evaluating on Kaggle"):
#   .\kaggle\eval\push.ps1 code   uploads the code and the made-up gold sets as the private dataset je-validator-code
#   .\kaggle\eval\push.ps1 run    starts run_eval.py on GPU T4 x2 as the private script je-validator-eval
# Needs the Kaggle CLI and your API token in %USERPROFILE%\.kaggle\kaggle.json. Never .env or real data.
param([Parameter(Mandatory = $true)][ValidateSet("code", "run")][string]$Step)

$root = (Resolve-Path "$PSScriptRoot\..\..").Path
$user = ((kaggle config view) -match "^- username: ").Split(" ")[-1]
if (-not $user) { throw "No Kaggle user name: is kaggle.json in $env:USERPROFILE\.kaggle?" }
$stage = Join-Path $env:TEMP "je-validator-kaggle\$Step"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory $stage | Out-Null

function Write-Json($value, $path) {  # UTF-8 without a BOM, which the Kaggle CLI can't read
    [IO.File]::WriteAllText($path, ($value | ConvertTo-Json -Depth 3))
}

if ($Step -eq "code") {
    # tar keeps the folder paths Linux needs; only these files go, so .env and eval_results stay here
    tar.exe -a -c -f "$stage\je-validator-code.zip" -C $root --exclude=__pycache__ je_validator sample_templates `
        goldset goldset_layout goldset_two_layouts goldset_pii requirements.txt requirements.lock requirements-ocr.txt `
        requirements-ocr.lock requirements-vl.txt requirements-vl.lock
    if ($LASTEXITCODE) { throw "tar failed" }
    $id = "$user/je-validator-code"
    Write-Json @{ title = "je-validator-code"; id = $id; licenses = @(@{ name = "unknown" }) } "$stage\dataset-metadata.json"
    kaggle datasets status $id *> $null
    # -t: keep the gold sets' Excel files as they are (Kaggle would convert them to CSV)
    if ($LASTEXITCODE) { kaggle datasets create -p $stage -t } else { kaggle datasets version -p $stage -m "code update" -t }
} else {
    Copy-Item "$PSScriptRoot\run_eval.py" $stage
    Write-Json @{ id = "$user/je-validator-eval"; title = "je-validator-eval"; code_file = "run_eval.py"
                  language = "python"; kernel_type = "script"; is_private = $true; enable_gpu = $true
                  enable_internet = $true; dataset_sources = @("$user/je-validator-code") } "$stage\kernel-metadata.json"
    kaggle kernels push -p $stage --accelerator NvidiaTeslaT4  # the P100 is too old for PaddlePaddle
}
