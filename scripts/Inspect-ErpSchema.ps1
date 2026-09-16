param(
    [string]$Server = 'lpc:.\ERPLOCAL',
    [string]$Database = 'ERP_Local',
    [string]$OutputDirectory = '.local/discovery'
)

$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder
$builder['Data Source'] = $Server
$builder['Initial Catalog'] = $Database
$builder['Integrated Security'] = $true
$builder['Connect Timeout'] = 10
$builder['Application Name'] = 'ERP Assistant Schema Discovery'
$connection = New-Object System.Data.SqlClient.SqlConnection $builder.ConnectionString
$queries = [ordered]@{
    tables = @'
SELECT s.name AS schema_name, t.name AS table_name,
       COALESCE(p.row_count, 0) AS approximate_rows
FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
LEFT JOIN (SELECT object_id, SUM(rows) AS row_count FROM sys.partitions
           WHERE index_id IN (0, 1) GROUP BY object_id) p ON p.object_id = t.object_id
WHERE t.is_ms_shipped = 0 ORDER BY s.name, t.name
'@
    columns = @'
SELECT s.name AS schema_name, t.name AS table_name, c.column_id, c.name AS column_name,
       ty.name AS data_type, c.max_length, c.precision, c.scale, c.is_nullable,
       c.is_identity, CAST(ep.value AS nvarchar(4000)) AS description
FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.extended_properties ep ON ep.major_id = t.object_id
     AND ep.minor_id = c.column_id AND ep.name = 'MS_Description' AND ep.class = 1
WHERE t.is_ms_shipped = 0 ORDER BY s.name, t.name, c.column_id
'@
    keys = @'
SELECT s.name AS schema_name, t.name AS table_name, i.name AS index_name,
       i.is_primary_key, i.is_unique, ic.key_ordinal, c.name AS column_name
FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.indexes i ON i.object_id = t.object_id AND (i.is_primary_key = 1 OR i.is_unique = 1)
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE t.is_ms_shipped = 0 AND ic.key_ordinal > 0
ORDER BY s.name, t.name, i.name, ic.key_ordinal
'@
    relations = @'
SELECT fk.name AS constraint_name, OBJECT_SCHEMA_NAME(fk.parent_object_id) AS schema_name,
       OBJECT_NAME(fk.parent_object_id) AS table_name, pc.name AS column_name,
       OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS referenced_schema,
       OBJECT_NAME(fk.referenced_object_id) AS referenced_table, rc.name AS referenced_column
FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fc ON fc.constraint_object_id = fk.object_id
JOIN sys.columns pc ON pc.object_id = fc.parent_object_id AND pc.column_id = fc.parent_column_id
JOIN sys.columns rc ON rc.object_id = fc.referenced_object_id AND rc.column_id = fc.referenced_column_id
ORDER BY schema_name, table_name, constraint_name
'@
}
try {
    $connection.Open()
    foreach ($entry in $queries.GetEnumerator()) {
        $command = $connection.CreateCommand()
        $command.CommandTimeout = 30
        $command.CommandText = $entry.Value
        $reader = $command.ExecuteReader()
        $table = New-Object System.Data.DataTable
        $table.Load($reader)
        $table | Export-Csv -LiteralPath (Join-Path $OutputDirectory ($entry.Key + '.csv')) -NoTypeInformation -Encoding UTF8
        Write-Output ($entry.Key + ': ' + $table.Rows.Count)
        $command.Dispose()
    }
} finally {
    $connection.Dispose()
}
