param([string]$OutputDirectory = '.local/discovery')
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$connection = New-Object System.Data.SqlClient.SqlConnection 'Server=lpc:.\ERPLOCAL;Database=ERP_Local;Integrated Security=True;Connect Timeout=10;Application Name=ERP Assistant Profiling'
$queries = [ordered]@{
    dictionary = @'
SELECT RTRIM(d.DjEName) AS document_code, RTRIM(d.DjCName) AS document_name,
       x.XmPosition AS position, x.XmEName AS field_name, x.XmCName AS field_label
FROM dbo.DjDy d JOIN dbo.XmDy x ON x.YyDm=d.YyDm AND x.DjDm=d.DjDm
WHERE RTRIM(d.DjEName) IN ('XSDD','XSTHD','CGDD','HGGOUHDWDA','HGGHDWDA','HGJYSPDA','YWSK')
ORDER BY d.DjEName, x.XmPosition, x.XmDm
'@
    sales_quality = @'
SELECT COUNT(*) AS order_count, MIN(CJRQ) AS earliest_created_at, MAX(CJRQ) AS latest_created_at,
       SUM(CASE WHEN CJRQ IS NULL THEN 1 ELSE 0 END) AS missing_date,
       SUM(CASE WHEN ZJE IS NULL THEN 1 ELSE 0 END) AS missing_amount,
       SUM(CASE WHEN ZJE < 0 THEN 1 ELSE 0 END) AS negative_amount,
       SUM(CASE WHEN NULLIF(RTRIM(DWBH),'') IS NULL THEN 1 ELSE 0 END) AS missing_buyer
FROM dbo.XSDDH
'@
    sales_states = @'
SELECT RTRIM(DjState) AS document_state, DDZT AS order_state, COUNT(*) AS order_count
FROM dbo.XSDDH GROUP BY DjState, DDZT ORDER BY order_count DESC
'@
    line_quality = @'
SELECT COUNT(*) AS line_count,
       SUM(CASE WHEN h.DjLsh IS NULL THEN 1 ELSE 0 END) AS orphan_lines,
       SUM(CASE WHEN b.JE IS NULL THEN 1 ELSE 0 END) AS missing_amount,
       SUM(CASE WHEN b.SL IS NULL THEN 1 ELSE 0 END) AS missing_quantity,
       SUM(CASE WHEN b.JE <> b.SL*b.DJ THEN 1 ELSE 0 END) AS price_quantity_mismatch
FROM dbo.XSDDB b LEFT JOIN dbo.XSDDH h ON h.DjLsh=b.DjLsh
'@
    header_line_reconciliation = @'
SELECT COUNT(*) AS order_count,
       SUM(CASE WHEN b.DjLsh IS NULL THEN 1 ELSE 0 END) AS orders_without_lines,
       SUM(CASE WHEN h.ZJE <> b.line_amount THEN 1 ELSE 0 END) AS different_amount_orders,
       MAX(ABS(h.ZJE-b.line_amount)) AS maximum_absolute_difference
FROM dbo.XSDDH h LEFT JOIN (SELECT DjLsh, SUM(JE) AS line_amount FROM dbo.XSDDB GROUP BY DjLsh) b ON b.DjLsh=h.DjLsh
'@
    buyer_links = @'
SELECT COUNT(*) AS orders,
       SUM(CASE WHEN a.DjLsh IS NOT NULL THEN 1 ELSE 0 END) AS matched_by_internal_id,
       SUM(CASE WHEN b.DjLsh IS NOT NULL THEN 1 ELSE 0 END) AS matched_by_business_code
FROM dbo.XSDDH h
LEFT JOIN dbo.HGGOUHDWDAH a ON RTRIM(h.DWBH)=CAST(a.DjLsh AS varchar(30))
LEFT JOIN dbo.HGGOUHDWDAH b ON h.DWBM=b.DWBM
'@
    product_links = @'
SELECT COUNT(*) AS lines,
       SUM(CASE WHEN a.DjLsh IS NOT NULL THEN 1 ELSE 0 END) AS matched_by_business_code
FROM dbo.XSDDB b LEFT JOIN dbo.HGJYSPDAH a ON b.SPBM=a.SPBM
'@
    return_links = @'
SELECT COUNT(*) AS returns,
       SUM(CASE WHEN h.DjLsh IS NOT NULL THEN 1 ELSE 0 END) AS matched_by_order_number
FROM dbo.XSTHDH r LEFT JOIN dbo.XSDDH h ON r.DDBH=h.BJDH
'@
    source_dates = @'
SELECT YEAR(CJRQ) AS year, MONTH(CJRQ) AS month, COUNT(*) AS order_count
FROM dbo.XSDDH GROUP BY YEAR(CJRQ), MONTH(CJRQ) ORDER BY year, month
'@
    amount_fields = @'
SELECT COUNT(*) AS orders,
       SUM(CASE WHEN DKJE IS NULL THEN 1 ELSE 0 END) AS missing_received_amount,
       SUM(CASE WHEN DKJE > 0 THEN 1 ELSE 0 END) AS positive_received_amount,
       COUNT(DISTINCT DWBM) AS distinct_buyer_codes
FROM dbo.XSDDH
'@
    returns_quality = @'
SELECT COUNT(*) AS return_documents, MIN(CJRQ) AS earliest_created_at, MAX(CJRQ) AS latest_created_at,
       SUM(CASE WHEN NULLIF(RTRIM(DDBH),'') IS NULL THEN 1 ELSE 0 END) AS missing_original_order
FROM dbo.XSTHDH
'@
    receipt_quality = @'
SELECT COUNT(*) AS receipt_documents, MIN(SKRQ) AS earliest_receipt_at, MAX(SKRQ) AS latest_receipt_at
FROM dbo.YWSKH
'@
}
# Counts only: exclude personal details, credentials and free-text business remarks.
try {
    $connection.Open()
    foreach ($entry in $queries.GetEnumerator()) {
        $command = $connection.CreateCommand()
        $command.CommandTimeout = 30
        $command.CommandText = $entry.Value
        $table = New-Object System.Data.DataTable
        $table.Load($command.ExecuteReader())
        $table | Export-Csv -LiteralPath (Join-Path $OutputDirectory ($entry.Key + '.csv')) -NoTypeInformation -Encoding UTF8
        Write-Output ($entry.Key + ': ' + $table.Rows.Count)
        $command.Dispose()
    }
} finally { $connection.Dispose() }
