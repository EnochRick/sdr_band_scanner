# SDR FCC Band Scanner

A terminal program that sweeps FCC-allocated frequency bands with a software-defined radio, detects signals above a noise floor, and shows them as a live, scrollable graph. Each signal is grouped under its band, labelled with the band's FCC allocation, and stamped with the last time it was seen. Results are also written to a text file as the scan runs.

The program is **receive-only**. It reports RF energy and does not demodulate or decode any transmissions.

---

## 1. What you need

| Item | Notes |
|---|---|
| An SDR | Anything SoapySDR supports: RTL-SDR, HackRF, Airspy, SDRplay, BladeRF, LimeSDR, USRP, and others. |
| Debian (or a Debian-based distro) | Instructions below use `apt`. |
| Python 3 | The system `python3` is fine. |
| SoapySDR | System packages plus the module for your radio. |
| `numpy`, `rich` | Installed inside the virtual environment (or via apt). |

No radio yet? Use `--simulate` (see section 5) to try everything with synthetic data.

---

## 2. Installing SoapySDR and your radio's driver module (one time, needs sudo)

The SoapySDR Python bindings are a **system library**. They are not installable with `pip`, so they come from apt.

```bash
sudo apt update
sudo apt install python3-soapysdr soapysdr-tools
```

Then install the module for your radio:

| Radio | Package |
|---|---|
| RTL-SDR | `soapysdr-module-rtlsdr` (also install `rtl-sdr`) |
| HackRF | `soapysdr-module-hackrf` |
| Airspy / Airspy HF+ | `soapysdr-module-airspy` / `soapysdr-module-airspyhf` |
| BladeRF | `soapysdr-module-bladerf` |
| LimeSDR | `soapysdr-module-lms7` |
| USRP | `soapysdr-module-uhd` |

Example for an RTL-SDR:

```bash
sudo apt install soapysdr-module-rtlsdr rtl-sdr
```

### RTL-SDR extras (USB permissions and kernel driver)

1. Stop Linux from grabbing the dongle as a TV tuner:

   ```bash
   echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtl.conf
   ```

2. Let your user access the device (the `rtl-sdr` package installs the udev rules):

   ```bash
   sudo usermod -aG plugdev $USER
   ```

3. **Log out and back in** (or reboot), then unplug and replug the dongle.

### Verify the radio is visible

```bash
SoapySDRUtil --find
```

Your radio should be listed with a line like `driver = rtlsdr`. If it isn't, fix this before going further (see Troubleshooting).

> Note: the command is `SoapySDRUtil`. An earlier message in this project called it `SoapySDR_util`, which was wrong.

---

## 3. Python virtual environment

The venv **must be created with `--system-site-packages`**, otherwise it cannot see the apt-installed SoapySDR bindings.

```bash
cd ~/apps/sdrScanningTool            # wherever you keep the script

python3 -m venv --system-site-packages venv
source venv/bin/activate

pip install numpy rich
```

You'll see `(venv)` in your prompt when it's active. Activate it each time you open a new terminal:

```bash
cd ~/apps/sdrScanningTool
source venv/bin/activate
```

Leave it with `deactivate`.

### Optional: `requirements.txt`

```text
numpy
rich
```

```bash
pip install -r requirements.txt
```

### Alternative: no pip at all

```bash
sudo apt install python3-numpy python3-rich
```

A `--system-site-packages` venv (or no venv) will see these.

### Check the environment

```bash
python -c "import SoapySDR, numpy, rich; print('all imports OK')"
python -c "import SoapySDR; print(SoapySDR.Device.enumerate())"
```

The second command should print your radio.

---

## 4. Files

| File | Purpose |
|---|---|
| `sdr_band_scanner.py` | The program. (Any filename works, so substitute yours in the commands below.) |
| `sdr_scanner.conf` | Band selection, created next to the script on first run. |
| `sdr_scan_YYYYmmdd_HHMMSS.txt` | Results report, one per run, created in the directory you launch from. |
| `*.csv` | Optional detection log (`--log`). |

---

## 5. Running it

```bash
# Try it with no hardware (synthetic carriers, instant demo)
python sdr_band_scanner.py --simulate

# RTL-SDR, automatic noise floor, VHF/UHF only (good first real test)
python sdr_band_scanner.py --device driver=rtlsdr --gain 30 --min-mhz 85 --max-mhz 500

# RTL-SDR, fixed threshold of -55 dBFS
python sdr_band_scanner.py --device driver=rtlsdr --gain 30 --noise-floor -55

# HackRF, faster sweep with a wider sample rate, single pass
python sdr_band_scanner.py --device driver=hackrf --rate 10e6 --gain 24 --once

# Pick the report filename and also log detections to CSV
python sdr_band_scanner.py --device driver=rtlsdr --output scan.txt --log scan.csv
```

Start with a **narrow frequency range** so you get a sweep back quickly, then widen it. A full 24 MHz to 1.7 GHz sweep on an RTL-SDR at 2.4 MS/s takes many minutes.

---

## 6. First run: band-selection menu

On the first run there is no `sdr_scanner.conf`, so a full-screen checklist appears listing every band your radio can tune (all ticked by default).

| Key | Action |
|---|---|
| `↑` `↓` (or `k` `j`) | Move |
| `Space` | Tick or untick the highlighted band |
| `a` / `c` / `i` | Select all / clear all / invert |
| `PgUp` `PgDn`, `Home` `End` | Move faster |
| `Enter` | Save and start scanning (needs at least one band ticked) |
| `q` | Quit without scanning |

Your choices are saved to `sdr_scanner.conf`. On later runs it is loaded silently and the scan starts straight away.

| To do this | Do this |
|---|---|
| Change your selection | `python sdr_band_scanner.py --menu` |
| Show the menu on every start | Set `show_menu = always` in the conf |
| Use a different conf file | `--config /path/to/file.conf` |
| Ignore the conf and scan everything | `--ignore-config` |
| Start over | Delete `sdr_scanner.conf` |

The conf file is plain text and safe to edit by hand:

```ini
[settings]
show_menu = first-run

[bands]
88-108 = on   # FM Broadcast - FM radio broadcasting
108-118 = off   # VOR / ILS - Aeronautical radionavigation...
```

Notes:
- Each line is keyed `<low MHz>-<high MHz> = on|off`. The text after `#` is only a comment.
- Bands missing from the file default to **on**.
- `--min-mhz`, `--max-mhz`, and `--only` still apply on top of the saved selection, and the menu only lists bands that pass them.
- If the conf can't be parsed, it is renamed to `.bak` and a fresh one is created.

---

## 7. The live screen

Scanning runs in the background while the screen stays interactive. Each band that has signals gets a panel: the title shows the band, its range, and its measured noise floor and threshold; the subtitle shows the FCC allocation; each row is a bar graph with frequency, level, bandwidth, and last-seen time.

Bar colours show how recently a signal was seen: **green** within 30 seconds (`--live-secs`), **yellow** within 5 minutes, **grey** older.

| Key | Action |
|---|---|
| `↑` `↓` (or `k` `j`) | Scroll one line |
| `PgUp` `PgDn` (or `b` / space) | Scroll one page |
| `Home` `End` (or `g` `G`) | Jump to top or bottom |
| `n` / `p` (or `Tab` / `Shift-Tab`) | Next or previous band panel |
| `f` | **Follow** mode: the view tracks the newest detection. Scrolling turns it off; `f` turns it back on. |
| `a` | Also list bands with no signals as one-line rows, with a `»` marker on the band being scanned |
| `q` or Ctrl-C | Quit. The full results are printed to the terminal and saved to the report file. |

- Your place is anchored to the band at the top of the view, so new signals appearing above it don't shift what you're reading.
- With `--once`, the screen stays open after the sweep so you can review it. Press `q` to leave.
- Many terminals turn the mouse wheel into arrow keys on a full-screen view, so the wheel may scroll too.
- If output is piped or redirected there is no interactive screen: it just scans and prints the results at the end.
- Interactive keys work on Linux and macOS terminals. On Windows the screen shows but keys aren't read.
- A small terminal window leaves little room for panels. The header takes several rows, especially when the report path is long.

---

## 8. Noise floor and detection

Signals are reported when their peak exceeds a threshold, measured in **dBFS per FFT bin** (0 dBFS is a full-scale tone; typical noise is around -70 dBFS).

- **Automatic (default).** Leave out `--noise-floor` and the scanner estimates the floor separately for each band, then adds a margin.
  - The floor is the 25th percentile of the band's spectrum (`--floor-percentile`), which stays on the noise even when much of the band is occupied.
  - The margin is 6 dB (`--floor-margin`).
- **Fixed.** `--noise-floor -55` uses one threshold for every band.

Tuning tips:
- Lots of false hits (USB noise spurs, tuner spurs, gain ripple): raise the margin (`--floor-margin 10`) or lower `--gain`.
- Weak signals missed: lower the margin.
- A band that is almost entirely signal (a fully occupied TV block) makes the auto estimate read high. Lower `--floor-percentile` to about 10, or use a fixed floor for that range.
- Strong signals can overload the receiver and create phantom signals elsewhere. If you see mirror-image or spread-out hits around a very strong signal, lower the gain.
- Changing `--rate` or `--fft` changes the per-bin noise level, so re-check your threshold.
- Levels are uncalibrated dBFS. If you've calibrated your radio, `--cal-offset` adds a dB offset to every reading (for example to convert to dBm).

---

## 9. Output files

### Results report (`sdr_scan_YYYYmmdd_HHMMSS.txt`)

Written continuously while the scan runs, so you can follow it with `tail -f sdr_scan_*.txt`. Disable with `--no-output`; choose a name with `--output`.

Contents:
- a header with the settings and noise-floor mode;
- one line per band scanned, showing its floor, threshold, and signal count;
- one `[SIGNAL]` line per detection, with frequency, level, bandwidth, band, and FCC allocation;
- after each sweep, a table of every tracked signal with first seen, last seen, and hit count.

### CSV log (`--log file.csv`)

One row per detection: time, frequency (MHz), level (dBFS), bandwidth (kHz), band, allocation.

---

## 10. All options

| Option | Default | Meaning |
|---|---|---|
| `--noise-floor DB` | auto | Fixed threshold in dBFS. Omit for automatic per-band estimation. |
| `--floor-margin DB` | 6 | Auto mode: dB above the estimated floor a signal must reach. |
| `--floor-percentile P` | 25 | Auto mode: percentile of each band's spectrum used as the floor. |
| `--device ARGS` | first device | SoapySDR device string, e.g. `driver=rtlsdr`, `driver=hackrf`. |
| `--rate HZ` | 2.4e6 | Sample rate. |
| `--gain DB` | auto | RF gain. |
| `--fft N` | 2048 | FFT size. Larger gives finer resolution. |
| `--dwell S` | 0.1 | Seconds captured per tile. Longer is more sensitive to intermittent signals but slower. |
| `--cal-offset DB` | 0 | Added to every reading. |
| `--dc-bins N` | 4 | Bins masked around each tile's centre to hide the DC spike (0 disables). |
| `--resolution-khz K` | 25 | Detections closer than this are treated as the same signal. |
| `--min-mhz` / `--max-mhz` | none | Skip bands below or above this frequency. |
| `--only A,B` | none | Scan only bands whose name or use contains one of these words. |
| `--bands-file CSV` | built-in table | Custom band table (see below). |
| `--once` | off | Single sweep, then stay open for review. |
| `--live-secs S` | 30 | A signal seen within this long is drawn green. |
| `--bar-span DB` | 40 | dB above the floor that fills a full bar. |
| `--bar-width N` | 40 | Maximum bar width in characters. |
| `--log FILE` | none | Append detections to a CSV file. |
| `--output FILE` | auto-named | Results text file. |
| `--no-output` | off | Don't write the results text file. |
| `--menu` | off | Show the band menu even if a saved selection exists. |
| `--config FILE` | next to script | Band-selection config file. |
| `--ignore-config` | off | Skip the menu and config; scan everything that passes the filters. |
| `--simulate` | off | Use synthetic data, no radio needed. |

### Custom band table

`--bands-file mybands.csv` replaces the built-in table. Columns:

```csv
low_mhz,high_mhz,name,use
88,108,FM Broadcast,FM radio broadcasting
162.4,162.55,NOAA Weather Radio,NOAA Weather Radio broadcasts
```

---

## 11. Hardware notes

| Radio | Approximate tuning range | Suggested `--rate` |
|---|---|---|
| RTL-SDR (R820T/R828D) | ~24 MHz to ~1.7 GHz | 2.4e6 (stay at or below 2.56e6) |
| HackRF One | 1 MHz to 6 GHz | 10e6 to 20e6 |
| Airspy | model dependent | per model |

- Bands outside your radio's range are skipped automatically, and bands that straddle the edge are clipped. The HF bands (below 24 MHz) need an upconverter on an RTL-SDR.
- The built-in table goes up to 7.125 GHz, but a HackRF stops at 6 GHz.
- A wider sample rate covers each band in fewer tiles, so it sweeps faster, at the cost of a higher noise floor per bin and more USB bandwidth.
- Use an antenna suited to the bands you care about. A small whip hears FM and VHF far better than microwave bands.

---

## 12. Troubleshooting

| Symptom | Fix |
|---|---|
| `Could not open SDR: No module named 'SoapySDR'` | Install `python3-soapysdr` (section 2). If you're in a venv, recreate it with `--system-site-packages` (section 3). |
| `ModuleNotFoundError: No module named 'rich'` (or `numpy`) | Activate the venv and run `pip install numpy rich`, or install the apt packages. |
| Radio not listed by `SoapySDRUtil --find` | Check the USB connection, install the matching `soapysdr-module-*` package, and for RTL-SDR confirm the DVB blacklist, `plugdev` group membership, and a fresh login and replug. |
| `Could not open SDR` or device busy | Close anything else using the radio (`gqrx`, `rtl_tcp`, other scripts). |
| `usb_claim_interface` or permission errors | udev rules or group membership are missing. See the RTL-SDR extras in section 2. |
| Nothing detected | Check the antenna, raise `--gain`, lower `--floor-margin`, or use a fixed `--noise-floor` a few dB above your noise. |
| Far too many detections | Raise `--floor-margin`, lower `--gain`, or use a fixed higher `--noise-floor`. |
| Scan is very slow | Narrow the range with `--min-mhz` / `--max-mhz` or `--only`, untick bands in the menu, shorten `--dwell`, or use a wider `--rate` if your radio supports it. |
| Menu or scroll keys do nothing | Needs a real interactive terminal on Linux or macOS. Piped output and Windows have no key input. |
| "Every band is switched off" | All bands are `off` in `sdr_scanner.conf`. Run with `--menu` or edit the file. |
| Report file in an unexpected place | It is created in the directory you launched from. Use `--output /full/path.txt` to choose. |
| Messages about missing bands | Bands outside your radio's range are skipped automatically and listed in a startup message. |

---

## 13. Caveats

- **The FCC allocation table is a condensed summary** of the Table of Frequency Allocations (47 CFR 2.106), written from general knowledge. Boundaries and descriptions are simplified. The real table has many co-primary and secondary services, footnotes, and regional rules. Verify against the official FCC table before relying on any label, or supply your own via `--bands-file`.
- Detection is energy-based: a hit means something was transmitting above your threshold, not that it is the labelled service. Spurious signals from your own radio or computer can appear.
- Levels are relative (dBFS) unless you calibrate and set `--cal-offset`.
- Receiving is generally legal, but rules on listening to or acting on certain transmissions vary by jurisdiction. This tool only measures signal energy.
