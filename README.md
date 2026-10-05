# Argentina 2027 election monitor

Streamlit app that tracks the 2027 Argentine presidential election through prediction
markets (Polymarket), opinion polls and Universidad Torcuato Di Tella's government (ICG)
and consumer (ICC) confidence indices. Every chart has an Excel download.

## Structure

```
app.py                  Streamlit app (all sections)
monitor/icg.py          ICG microdata (.dta) -> monthly series
monitor/icc.py          ICC workbook (.xls) -> monthly series
monitor/polymarket.py   Polymarket public API
monitor/polls.py        Poll sheet reader, trend line and monthly average
monitor/utils.py        File discovery and Excel export
data/icg/               ICG microdata (one .dta file)
data/icc/               ICC workbook (one .xls/.xlsx file)
data/polls/polls.csv    Fallback poll sheet (used if no Google Sheet is configured)
templates/              Poll sheet template
```

## Monthly update

- ICG: upload the new `.dta` to `data/icg/` and delete the old one.
- ICC: upload the new `Serie_ICC_MM-YY.xls` to `data/icc/` and delete the old one.
- Polls: add rows to the Google Sheet. Nothing to do on GitHub.
- Polymarket: automatic.

## Polls from Google Sheets

Publish the `polls` sheet as CSV (File > Share > Publish to web) and add the link as a
secret in Streamlit Community Cloud:

```
POLLS_CSV_URL = "https://docs.google.com/spreadsheets/d/e/.../pub?gid=...&single=true&output=csv"
```

## Run locally

```
pip install -r requirements.txt
streamlit run app.py
```
