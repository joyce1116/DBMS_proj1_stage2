# Load with: . .\scripts\http_helpers.ps1
# Windows PowerShell 5 otherwise decodes JSON without a charset as Latin-1.
function Invoke-Utf8RestMethod {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [string]$Uri,
        [string]$Method = 'Get',
        [hashtable]$Headers = @{},
        [string]$ContentType = 'application/json; charset=utf-8',
        [object]$Body
    )

    $requestArguments = @{
        Uri = $Uri
        Method = $Method
        Headers = $Headers
        ContentType = $ContentType
        UseBasicParsing = $true
        ErrorAction = 'Stop'
    }
    if ($PSBoundParameters.ContainsKey('Body')) {
        if ($Body -is [byte[]]) {
            $requestArguments.Body = $Body
        } else {
            $requestArguments.Body = [Text.Encoding]::UTF8.GetBytes([string]$Body)
        }
    }
    $response = Invoke-WebRequest @requestArguments
    $jsonText = [Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray())
    if ($jsonText.Length -gt 0) {
        $jsonText | ConvertFrom-Json
    }
}
