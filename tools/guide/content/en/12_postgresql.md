# 12. Databases: the PostgreSQL page

The PostgreSQL page (menu **Database ▾ → PostgreSQL**) reads time series from a PostgreSQL database (including TimescaleDB), **read-only**, with the same charts, periods, data reduction, zoom with more detail, automatic refresh, X1 ↔ X2 analysis and export as the InfluxDB page. Two tabs: **Chart** (Grafico) and **Table and query** (Tabella e query).

## Connecting

In **Server connection** enter address, port, database, user and password, the SSL mode (if available, disabled, required, required with certificate verification) and the time zone of timestamps without zone (UTC or PC time). The password stays in the service (data folder) and is never sent to the page or written in exported files; «Forget password» deletes it. A user with read-only permissions is recommended.

## Chart tab

![pg_chart](PostgreSQL page, Chart tab: four columns of the table opened as series.)

| N. | Control |
|---|---|
| 1 | **Server connection**. |
| 2 | **Table or view**: automatic list of readable tables, views and materialised views with a time column and numeric or boolean columns. |
| 3 | **Time column** (timestamp, timestamp with zone, date): proposed automatically and editable. |
| 4 | **Column with the signal name**: «None: one column per signal» (each numeric column is a series) or a column with the names (one row per signal and instant; each name is a series). |
| 5 | **Refresh the list**. |
| 6 | **Filter** on tables, columns and signals. |
| 7 | **Series** of the table, with the type of each column. |
| 8 | **Period** (5 min … 30 d) or «Interval» of your choice. |
| 9 | **Data reduction**: Automatic (mean), mean, minimum, maximum, last, first, median, sum, all points. |
| 10 | **Open in chart**. |
| 11 | **Chart** tab. |
| 12 | **Table and query** tab. |

With few points in the period all original values arrive; with many points the server calculates the chosen function over windows of round duration, with the point at the centre of the window; booleans become 0/1. Table and column names are always enclosed in quotes and values are passed as parameters.

## Table and query tab: table view

![pg_table](Table view of the selected table.)

| N. | Control |
|---|---|
| 1 | **Selected table**: name of the table or view read. |
| 2 | **Only the chosen period** (Solo il periodo scelto): checkbox that limits the rows to the period chosen for the chart (otherwise the whole table is read). |
| 3 | **Order**: «Most recent first» (or oldest first); you can also sort by clicking a column header. |
| 4 | **Rows per page**: 100, 500, 1,000 or 5,000. |
| 5 | **Run** (refreshes the page). |
| 6 | **Next / previous page**. |
| 7 | **Export page to CSV** (and **Copy**): save or copy the rows shown, with values as displayed. |
| 8 | **Table**: all columns, with the type under the name, right-aligned numbers, highlighted null values and row number. |

To export all the data of the series use **Export…** in the Chart tab (CSV, Parquet, FDA, RBF).

## SQL queries and saved queries

![pg_query](SQL query written in the editor, run and saved.)

| N. | Control |
|---|---|
| 1 | **Saved queries**: list; a click reloads name and text into the editor. |
| 2 | **Name** of the query. |
| 3 | **Save**: saves name, text and rows per page; if the name already exists, pressing Save a second time replaces the query. |
| 4 | **New**: clears the editor. |
| 5 | **Delete** the selected saved query. |
| 6 | **SQL editor**: Ctrl+Enter runs. |
| 7 | **Run**. |
| 8 | **Result**: same table as the table view, with types, pages and execution time. Errors indicate the character of the query they refer to. |

Saved queries are stored in the service data folder (`postgres_queries.json`, up to 200); the last text written is remembered even without saving.

> **Read-only guaranteed.** Only single statements starting with SELECT, WITH, VALUES or TABLE are accepted. The query runs in a read-only transaction behind a server-side cursor (only the requested page is transferred) with a maximum time of 60 s: a DELETE, an UPDATE or a WITH … DELETE are rejected by the server even if they bypassed the text check.
