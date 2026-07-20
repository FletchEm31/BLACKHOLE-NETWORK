#!/bin/bash
# Monthly-chunked ASOS 1-min/5-min fetch from IEM, per the safety guidance in
# "ASOS 1M-5M Automation Curl Data Reqauests.txt": one station, one month,
# one resolution at a time, saved to separate files, validated before merge.
#
# Fixes two bugs found in that doc's own curl templates: the Denver and DFW
# commands were missing the station= parameter entirely (would have pulled
# whatever IEM defaults to, not the intended station).
#
# Usage: asos_monthly_fetch.sh <STATION> <1min|5min> <start_year> <end_year> <out_dir>
set -uo pipefail

STATION="$1"
RES="$2"          # 1min or 5min
START_YEAR="$3"
END_YEAR="$4"
OUT_DIR="$5"

mkdir -p "$OUT_DIR"

VARS="vars=tmpf&vars=dwpf&vars=sknt&vars=drct&vars=gust_drct&vars=gust_sknt&vars=ptype&vars=precip&vars=pres1&vars=pres2&vars=pres3"

for year in $(seq "$START_YEAR" "$END_YEAR"); do
  for month in $(seq 1 12); do
    # Don't request future months
    if [ "$year" -eq "$(date -u +%Y)" ] && [ "$month" -gt "$(date -u +%m | sed 's/^0//')" ]; then
      break
    fi

    # Compute next month/year for the end bound (exclusive, day1=1 hour=0 min=0)
    if [ "$month" -eq 12 ]; then
      end_year=$((year + 1)); end_month=1
    else
      end_year=$year; end_month=$((month + 1))
    fi

    out_file="${OUT_DIR}/${STATION}_${RES}_${year}_$(printf '%02d' "$month").csv"
    if [ -s "$out_file" ]; then
      echo "SKIP (exists): $out_file"
      continue
    fi

    url="https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?station=${STATION}&tz=UTC&year1=${year}&month1=${month}&day1=1&hour1=0&minute1=0&year2=${end_year}&month2=${end_month}&day2=1&hour2=0&minute2=0&${VARS}&sample=${RES}&what=download&delim=comma&gis=yes"

    result=$(curl -s -o "$out_file" -w '%{http_code} %{size_download}' "$url")
    http_code=$(echo "$result" | awk '{print $1}')
    size=$(echo "$result" | awk '{print $2}')

    if [ "$http_code" != "200" ] || [ "$size" -lt 50 ]; then
      echo "FAIL ${STATION} ${RES} ${year}-$(printf '%02d' "$month"): http=${http_code} size=${size}"
      rm -f "$out_file"
    else
      lines=$(wc -l < "$out_file")
      echo "OK   ${STATION} ${RES} ${year}-$(printf '%02d' "$month"): ${size} bytes, ${lines} lines"
    fi

    sleep 1.5   # be a good citizen of IEM's public server
  done
done

echo "=== ${STATION} ${RES} fetch complete ==="
