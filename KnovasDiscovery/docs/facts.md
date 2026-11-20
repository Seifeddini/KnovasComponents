# Facts the probes produce

Generated from the probes' `FACTS` (`python -m knovas_discovery.facts > docs/facts.md`); a test keeps the two equal
and checks that each probe produces exactly these facts. A catalogue condition (`detect`, `checks`) may name only a
fact listed here, by its dotted path; `knovas-discovery catalogue --check` enforces it. Facts are numbers, patterns
and database schema names: never a name of a person, client, file, folder or group (those go to `detail.json`).

Rules behind the guesses (`heuristics.py`, `probes/fileshare.py`, `probes/directory.py`):

- Table roles, in this order: **leftover** if the table has no key (primary key, else its first unique index) or its
  name speaks of a backup, copy, import, test or temporary table or ends in a date (6 to 8 digits); **software** if
  the name starts with SYS, CFG, LOG, AUDIT, SETTING, PARAM or LOCK, or speaks of locks (sperr), numbering
  (nummern), logins (ends in benutzer), sessions, or is only a version table; **records** if the key is made only of
  other tables' one-column keys (a link table); **lookup** if the key is one column and the table has at most 4
  columns, or at most 5 columns and at most 50 rows; else **records**.
- A key of several columns is never a lookup: such a row belongs to something else.
- A rights table is a records table whose name speaks of rights, access or locks (recht, berechtig, zugriff,
  permission, acl, sperr), and is not a SYS table: a lookup only lists kinds (areas of law, kinds of right).
- Names: "custom" does not count inside "customer", "eav" only as a word of its own, "temp" not inside "template",
  "test" not inside "testament".
- Directory: Administrator, Guest and krbtgt (relative ids 500 to 502) are not people; groups in CN=Builtin and the
  default groups in CN=Users (relative id below 1000, DnsAdmins, DnsUpdateProxy) are not the firm's groups.
- File share: the long-path limit (240) counts the path below the share, root included.

## environment

- `os`: operating system and release of the scanning machine
- `python`: Python version the tool runs on
- `in_container`: whether the tool runs inside a container
- `docker.present`: whether a docker command is on the PATH
- `docker.version`: the docker client's version, or null
- `cpus`: number of logical CPUs
- `memory_bytes`: physical memory in bytes, or null if it cannot be read
- `disk_free_bytes`: free disk space where the output folder is, in bytes
- `api_host_set`: whether intake.yaml names a Knovas API host
- `api_reachable`: whether a TCP connection to knovas.api_host:api_port succeeded (connect only, nothing sent)
- `api_error`: why the connection failed (a code), or null
- `proxy_vars`: names of the proxy environment variables that are set (never their values)

## fileshare

- `files`: files the account can see below the root
- `bytes`: their total size in bytes
- `folders`: folders below the root, including those that refuse the account
- `max_depth`: deepest folder level below the root (a folder directly in the root is level 1)
- `extensions`: files per extension, lower case with the dot; "" for files without one
- `connector_readable_files`: files with an extension the Knovas Connector reads (.md .txt .docx .pdf .eml .msg)
- `connector_readable_share`: connector_readable_files / files (null when there are no files)
- `age.newest_days`: days since the newest file was changed (by modification time, at scan time)
- `age.oldest_days`: days since the oldest file was changed
- `age.bands`: files per age band by modification time: <1y, 1-5y, 5-10y, >10y
- `refused_folders`: folders the account may not list (permission denied), the root included; their paths are in the detail
- `patterns.autodoc_names_share`: share of files whose name stem is <GUID>_<something>_<something> (AutoDoc); null when there are no files
- `patterns.range_folders`: folders named as a number range: six digits, a dash, six digits
- `patterns.office_lock_files`: files whose name starts with ~$ (Office lock files)
- `patterns.outlook_archives`: Outlook archive files (.pst, .ost)
- `patterns.dictation_files`: dictation files (.dss, .ds2, .dvf)
- `patterns.scanner_files`: scanner output: name starts with scan/scn/img and a number; .pdf .tif .tiff .jpg
- `patterns.no_extension_files`: files without an extension
- `patterns.long_paths`: files whose path below the share is longer than 240 characters
- `patterns.empty_files`: files of size 0
- `largest_file_bytes`: size of the largest file in bytes
- `listing.seconds`: seconds the listing took
- `listing.entries_per_second`: files and folders listed per second (null when nothing could be listed)
- `first_read_forecast.files_to_upload`: files the Connector would upload on its first read (connector_readable_files)
- `first_read_forecast.hours_at_4_per_minute`: hours the first read takes at 4 uploads per minute

## directory

- `is_active_directory`: the root DSE shows Active Directory (its capability OID or a defaultNamingContext)
- `people_enabled`: enabled user accounts that are people (not service or built-in accounts)
- `people_disabled`: disabled user accounts that are people (userAccountControl bit 2)
- `service_accounts`: accounts that look like services: name starts with svc, sa- or service, or in an OU named *Dienst* or *Service*
- `groups`: groups the firm made (not in CN=Builtin, not the default groups in CN=Users)
- `groups_empty`: of those, groups without any member
- `max_nesting`: longest chain of group-in-group among those groups (0: no group is in another)
- `computers`: computer accounts

## mssql

- `version`: SQL Server version (SERVERPROPERTY ProductVersion)
- `version_name`: the release that version belongs to (SQL Server 2019, 2022 ...)
- `edition`: SQL Server edition (SERVERPROPERTY Edition)
- `authentication`: windows (Windows logins only) or mixed (SQL and Windows logins), from SERVERPROPERTY IsIntegratedSecurityOnly
- `tables`: user tables in the database
- `table_names`: their names in upper case (schema-qualified when the schema is not dbo)
- `rows_total`: rows in all tables, from sys.partitions (no table is read)
- `record_rows`: rows in the tables whose role is records
- `foreign_keys`: declared foreign keys
- `views`: views the login can see
- `procedures`: stored procedures the login can see
- `record_tables`: tables whose guessed role is records
- `lookup_tables`: tables whose guessed role is lookup (a code and its text)
- `software_tables`: tables whose guessed role is software (settings, numbering, logins, locks)
- `leftover_tables`: tables whose guessed role is leftover (backups, imports, copies; or no key)
- `tables_with_rowversion`: tables with a rowversion (timestamp) column
- `tables_with_changed_at`: tables with a date column named like changed/entered (MutiertAm, ErfasstAm, ModifiedOn ...)
- `record_tables_without_change_marker`: record tables with neither a rowversion nor a changed-at column
- `tables_with_delete_flag`: tables with a yes/no column named like deleted/inactive/cancelled/archived
- `record_tables_without_delete_flag`: record tables without such a column: their rows disappear when removed
- `rights_tables`: record tables whose name speaks of rights, access or locks (not SYS tables)
- `custom_field_tables`: tables whose name speaks of custom fields (Zusatz, custom, Freifeld, EAV ...)
- `vocabulary.legal_practice`: how many of the stems AKTE, MANDAT, ADRESS, LEISTUNG, FRIST, HONORAR, RECHNUNG, BETEILIGT, DOKUMENT, TERMIN occur in table names
- `product`: the catalogue key of the product whose table signature matches, or null
- `table_list`: per table: name, rows, columns, key, foreign_keys, rowversion, changed_at, delete_flag, role

## m365

- `people`: enabled member accounts with at least one licence (guests, disabled and unlicensed accounts are not people)
- `licences`: licences in use per product (subscribedSkus: skuPartNumber -> consumedUnits)
- `sharepoint_sites`: SharePoint sites, without personal (OneDrive) sites
- `drives_bytes`: bytes used in the document libraries of those sites and in the people's OneDrives
- `exchange_online`: whether any account has an enabled Exchange Online plan
- `teams`: groups that are Microsoft Teams

## intake

- `category`: the category the client named (practice, dms, mail, accounting, time, collaboration, web-api, other)
- `product`: the product the client named: a catalogue key, a product name in lower case, or unknown
