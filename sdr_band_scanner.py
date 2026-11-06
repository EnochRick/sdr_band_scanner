#!/usr/bin/env python3
"""
sdr_band_scanner.py - Sweep FCC-allocated bands with an SDR and graph signals
found above a user-defined noise floor, in the terminal.

For every band in the allocation table the scanner tunes the SDR across the
band in overlapping tiles, builds a power spectrum (dBFS per FFT bin), and
reports any signal whose peak exceeds --noise-floor.  Each detected signal is
drawn as a bar inside a panel titled with the band name and its FCC allocation,
along with the last time that signal was seen.

This program is receive-only and reports RF energy; it does not demodulate
or decode any transmissions.

The scan runs in the background while the screen stays interactive (Linux/macOS
terminals). Keys:
  Up/Down, j/k      scroll one line        PgUp/PgDn, b/space   scroll one page
  Home/End, g/G     jump to top/bottom     n/p (Tab/Shift-Tab)  next/previous band panel
  f                 follow the newest activity (turned off when you scroll)
  a                 also list bands with no signals, and mark the band being scanned
  q or Ctrl-C       quit (the full results are printed and saved to the text file)

Band selection: on the first run a checklist menu lets you choose which bands to
scan (Space toggles, a/c/i = all/clear/invert, Enter saves and starts). The choice is
written to sdr_scanner.conf next to this script and reused on later runs. Open the
menu again with --menu, or set "show_menu = always" in the conf file.

Usage examples
--------------
  # Test the UI with no hardware attached; noise floor is found automatically
  python sdr_band_scanner.py --simulate

  # Same, but with a fixed threshold you choose
  python sdr_band_scanner.py --simulate --noise-floor -60

  # Auto floor, report written to a file of your choice
  python sdr_band_scanner.py --device driver=rtlsdr --output scan.txt

  # RTL-SDR, threshold -55 dBFS, 30 dB gain, VHF/UHF only
  python sdr_band_scanner.py --device driver=rtlsdr --gain 30 \
      --noise-floor -55 --min-mhz 100 --max-mhz 1000

  # HackRF, one sweep, log detections to CSV
  python sdr_band_scanner.py --device driver=hackrf --rate 10e6 --gain 24 \
      --noise-floor -60 --once --log detections.csv

Requirements: numpy, rich, and SoapySDR with the module for your radio
(not needed for --simulate).
"""
import argparse
import configparser
import csv
import datetime as dt
import os
import sys
import threading
import time
from typing import Dict, List, NamedTuple, Optional

import numpy as np
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.segment import Segment
from rich.style import Style
from rich.table import Table
from rich.text import Text


# --------------------------------------------------------------------------
# Band table
# --------------------------------------------------------------------------
class Band(NamedTuple):
    lo: float   # Hz
    hi: float   # Hz
    name: str
    use: str    # FCC allocation / typical use


# (low MHz, high MHz, short name, allocation / use)
# CONDENSED summary of the FCC Table of Frequency Allocations (47 CFR 2.106).
# Boundaries and descriptions are simplified; the full table has far more
# detail (footnotes, co-primary/secondary services, regional splits).
# Use --bands-file to supply your own CSV (low_mhz,high_mhz,name,use).
_BAND_TABLE = [
    (0.535, 1.705, "AM Broadcast", "AM radio broadcasting (MF)"),
    (1.8, 2.0, "160 m Amateur", "Amateur radio"),
    (3.5, 4.0, "80 m Amateur", "Amateur radio"),
    (5.3305, 5.4065, "60 m Amateur", "Amateur radio (channelized)"),
    (7.0, 7.3, "40 m Amateur", "Amateur radio"),
    (10.1, 10.15, "30 m Amateur", "Amateur radio"),
    (14.0, 14.35, "20 m Amateur", "Amateur radio"),
    (18.068, 18.168, "17 m Amateur", "Amateur radio"),
    (21.0, 21.45, "15 m Amateur", "Amateur radio"),
    (24.89, 24.99, "12 m Amateur", "Amateur radio"),
    (26.965, 27.405, "CB Radio", "Citizens Band personal radio, 40 channels (Part 95)"),
    (28.0, 29.7, "10 m Amateur", "Amateur radio"),
    (30.0, 50.0, "VHF Low Band", "Federal/non-federal land mobile and fixed"),
    (50.0, 54.0, "6 m Amateur", "Amateur radio"),
    (54.0, 72.0, "TV Ch 2-4", "Broadcast television (VHF-low)"),
    (72.0, 76.0, "VHF Fixed/Mobile", "Fixed/mobile; 75 MHz aeronautical markers"),
    (76.0, 88.0, "TV Ch 5-6", "Broadcast television (VHF-low)"),
    (88.0, 108.0, "FM Broadcast", "FM radio broadcasting"),
    (108.0, 118.0, "VOR / ILS", "Aeronautical radionavigation (VOR, ILS localizer)"),
    (118.0, 137.0, "Airband Voice", "Aeronautical mobile - air traffic control voice"),
    (137.0, 138.0, "Weather/Space Sats", "Meteorological-satellite and space-operation downlinks"),
    (138.0, 144.0, "Federal VHF", "Federal Government fixed/mobile"),
    (144.0, 148.0, "2 m Amateur", "Amateur radio"),
    (148.0, 150.8, "VHF Fed / MSS", "Federal fixed/mobile; mobile-satellite uplinks"),
    (150.8, 156.0, "VHF Land Mobile", "Public safety, business land mobile (incl. MURS)"),
    (156.0, 162.025, "Marine VHF", "Maritime mobile VHF channels"),
    (162.025, 162.4, "VHF Fixed/Mobile", "Federal/non-federal fixed and mobile"),
    (162.4, 162.55, "NOAA Weather Radio", "NOAA Weather Radio broadcasts"),
    (162.55, 174.0, "VHF Fixed/Mobile", "Federal/non-federal fixed and mobile, land mobile"),
    (174.0, 216.0, "TV Ch 7-13", "Broadcast television (VHF-high)"),
    (216.0, 220.0, "Fixed/Maritime", "Fixed and maritime mobile"),
    (220.0, 225.0, "1.25 m Amateur", "Amateur radio"),
    (225.0, 328.6, "Federal UHF Mil/Air", "Federal military aeronautical UHF"),
    (328.6, 335.4, "ILS Glide Slope", "Aeronautical radionavigation (ILS glideslope)"),
    (335.4, 406.0, "Federal UHF", "Federal fixed/mobile"),
    (406.0, 406.1, "Distress Beacons", "Mobile-satellite: 406 MHz emergency beacons"),
    (406.1, 420.0, "Federal UHF", "Federal fixed/mobile"),
    (420.0, 450.0, "70 cm Amateur", "Amateur (secondary) / Federal radiolocation"),
    (450.0, 470.0, "UHF Land Mobile", "Public safety, business land mobile, FRS/GMRS (462-467)"),
    (470.0, 608.0, "TV Ch 14-36", "Broadcast television (UHF)"),
    (608.0, 614.0, "Radio Astronomy", "Radio astronomy (TV channel 37)"),
    (614.0, 698.0, "600 MHz Mobile", "Mobile broadband (LTE/5G)"),
    (698.0, 806.0, "700 MHz", "Mobile broadband (LTE), FirstNet public safety"),
    (806.0, 824.0, "800 MHz SMR/PS", "Land mobile: 800 MHz public safety / SMR"),
    (824.0, 849.0, "Cellular Uplink", "Cellular mobile transmit"),
    (851.0, 869.0, "800 MHz SMR/PS", "Land mobile: 800 MHz public safety / SMR"),
    (869.0, 894.0, "Cellular Downlink", "Cellular base transmit"),
    (894.0, 902.0, "Land Mobile / NPCS", "Business land mobile, narrowband PCS"),
    (902.0, 928.0, "902 MHz ISM", "ISM (Part 15: LoRa, RFID, cordless), 33 cm amateur"),
    (928.0, 960.0, "Paging / Fixed", "Paging, fixed point-to-point, land mobile"),
    (960.0, 1164.0, "DME / SSR / ADS-B", "Aeronautical radionavigation (DME, transponders 1030/1090)"),
    (1164.0, 1215.0, "RNSS L5/E5", "GPS L5, Galileo E5, aeronautical radionavigation"),
    (1215.0, 1240.0, "RNSS L2", "GPS L2 (1227.6 MHz), GLONASS"),
    (1240.0, 1300.0, "23 cm Amateur", "Amateur / radiolocation / RNSS"),
    (1300.0, 1350.0, "L-band Radar", "Aeronautical radionavigation, long-range ATC radar"),
    (1350.0, 1427.0, "Federal L-band", "Federal fixed/mobile; 1400-1427 passive radio astronomy"),
    (1427.0, 1525.0, "L-band Mobile", "Fixed/mobile, telemetry, mobile broadband"),
    (1525.0, 1559.0, "MSS Downlink", "Mobile-satellite space-to-Earth (e.g. Inmarsat)"),
    (1559.0, 1610.0, "RNSS L1", "GPS L1 (1575.42 MHz), GLONASS L1, Galileo E1"),
    (1610.0, 1626.5, "MSS Iridium/Globalstar", "Mobile-satellite, radiodetermination"),
    (1626.5, 1660.5, "MSS Uplink", "Mobile-satellite Earth-to-space"),
    (1660.5, 1710.0, "Met-Aids / RadioAstro", "Radiosondes, met-satellite downlinks (GOES), radio astronomy"),
    (1710.0, 1755.0, "AWS-1 Uplink", "Mobile broadband uplink"),
    (1755.0, 1780.0, "AWS-3 Uplink", "Mobile broadband (shared with Federal)"),
    (1780.0, 1850.0, "Federal Fixed/Mobile", "Federal fixed/mobile"),
    (1850.0, 1915.0, "PCS Uplink", "Broadband PCS (mobile transmit)"),
    (1915.0, 1930.0, "PCS / Unlicensed PCS", "PCS, unlicensed PCS incl. DECT 6.0 (1920-1930)"),
    (1930.0, 1995.0, "PCS Downlink", "Broadband PCS (base transmit)"),
    (1995.0, 2025.0, "AWS-H / AWS-4 Uplink", "Mobile broadband uplink"),
    (2025.0, 2110.0, "Space Ops / BAS", "Space operations, Broadcast Auxiliary Service, cable relay"),
    (2110.0, 2155.0, "AWS-1 Downlink", "Mobile broadband downlink"),
    (2155.0, 2180.0, "AWS-3 Downlink", "Mobile broadband downlink"),
    (2180.0, 2200.0, "AWS-4 Downlink", "Mobile broadband downlink"),
    (2200.0, 2290.0, "Space Research", "Space research/operations, Federal fixed/mobile (S-band)"),
    (2290.0, 2300.0, "Deep Space", "Space research (deep space downlink)"),
    (2300.0, 2305.0, "13 cm Amateur", "Amateur radio"),
    (2305.0, 2320.0, "WCS", "Wireless Communications Service"),
    (2320.0, 2345.0, "Satellite Radio", "Digital audio radio satellite service (SDARS)"),
    (2345.0, 2360.0, "WCS", "Wireless Communications Service"),
    (2360.0, 2400.0, "Aero Telem / Amateur", "Aeronautical mobile telemetry, amateur"),
    (2400.0, 2483.5, "2.4 GHz ISM", "ISM (Part 15: Wi-Fi, Bluetooth, Zigbee), amateur"),
    (2483.5, 2495.0, "MSS / RDSS", "Mobile-satellite downlink, radiodetermination"),
    (2495.0, 2690.0, "BRS / EBS 2.5 GHz", "Broadband Radio Service / Educational Broadband"),
    (2690.0, 2700.0, "Radio Astronomy", "Passive Earth exploration / radio astronomy"),
    (2700.0, 2900.0, "S-band Radar", "Aeronautical radionavigation: ATC and weather radar"),
    (2900.0, 3100.0, "Maritime Radar", "Maritime radionavigation / radiolocation"),
    (3100.0, 3300.0, "Radiolocation", "Federal radiolocation"),
    (3300.0, 3450.0, "Radiolocation / Amateur", "Radiolocation; amateur (secondary)"),
    (3450.0, 3550.0, "3.45 GHz Mobile", "Mobile broadband (5G)"),
    (3550.0, 3700.0, "CBRS", "Citizens Broadband Radio Service (shared LTE/5G)"),
    (3700.0, 3980.0, "C-Band 5G", "Mobile broadband (5G C-band)"),
    (3980.0, 4200.0, "Satellite C-band", "Fixed-satellite space-to-Earth"),
    (4200.0, 4400.0, "Radio Altimeters", "Aeronautical radionavigation (radio altimeters)"),
    (4400.0, 4940.0, "Federal Fixed/Mobile", "Federal fixed/mobile, aeronautical telemetry"),
    (4940.0, 4990.0, "4.9 GHz Public Safety", "Public safety broadband"),
    (4990.0, 5000.0, "Radio Astronomy", "Radio astronomy"),
    (5000.0, 5150.0, "Aero Nav / MLS", "Aeronautical radionavigation, microwave landing, UAS links"),
    (5150.0, 5250.0, "U-NII-1", "Unlicensed Wi-Fi 5 GHz (Part 15)"),
    (5250.0, 5350.0, "U-NII-2A", "Unlicensed Wi-Fi 5 GHz (DFS)"),
    (5350.0, 5470.0, "Radiolocation / EESS", "Radiolocation, Earth exploration-satellite"),
    (5470.0, 5725.0, "U-NII-2C", "Unlicensed Wi-Fi 5 GHz (DFS), weather radar"),
    (5725.0, 5850.0, "U-NII-3 / 5.8 ISM", "Unlicensed Wi-Fi, ISM, amateur"),
    (5850.0, 5925.0, "U-NII-4 / ITS", "Wi-Fi (5850-5895) and C-V2X vehicle safety (5895-5925)"),
    (5925.0, 7125.0, "6 GHz U-NII-5..8", "Unlicensed Wi-Fi 6E/7, fixed microwave"),
]

DEFAULT_BANDS = [Band(lo * 1e6, hi * 1e6, n, u) for lo, hi, n, u in _BAND_TABLE]


def load_bands_file(path: str) -> List[Band]:
    bands = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            bands.append(Band(float(row["low_mhz"]) * 1e6, float(row["high_mhz"]) * 1e6,
                              row["name"].strip(), row["use"].strip()))
    return sorted(bands, key=lambda b: b.lo)


# --------------------------------------------------------------------------
# Receivers
# --------------------------------------------------------------------------
class SoapyReceiver:
    def __init__(self, device_args: str, rate: float, gain: Optional[float]):
        import SoapySDR
        from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32
        self.RX = SOAPY_SDR_RX
        self.sdr = SoapySDR.Device(device_args)
        self.sdr.setSampleRate(self.RX, 0, rate)
        self.rate = float(self.sdr.getSampleRate(self.RX, 0))
        if gain is None:
            try:
                self.sdr.setGainMode(self.RX, 0, True)
            except Exception:
                pass
        else:
            try:
                self.sdr.setGainMode(self.RX, 0, False)
            except Exception:
                pass
            self.sdr.setGain(self.RX, 0, gain)
        rngs = self.sdr.getFrequencyRange(self.RX, 0)
        self.fmin = min(r.minimum() for r in rngs)
        self.fmax = max(r.maximum() for r in rngs)
        self.stream = self.sdr.setupStream(self.RX, SOAPY_SDR_CF32)
        self.sdr.activateStream(self.stream)

    def tune(self, freq: float):
        self.sdr.setFrequency(self.RX, 0, freq)

    def capture(self, n: int) -> np.ndarray:
        buf = np.empty(n, np.complex64)
        got, failures = 0, 0
        while got < n:
            sr = self.sdr.readStream(self.stream, [buf[got:]], n - got, timeoutUs=500_000)
            if sr.ret > 0:
                got += sr.ret
                failures = 0
            else:
                failures += 1
                if failures > 20:
                    raise RuntimeError(f"SDR read failed repeatedly (code {sr.ret})")
        return buf

    def close(self):
        try:
            self.sdr.deactivateStream(self.stream)
            self.sdr.closeStream(self.stream)
        except Exception:
            pass


class SimReceiver:
    """Synthetic receiver: noise plus a handful of intermittent carriers."""
    TONES = [  # (Hz, dBFS, duty cycle)
        (98.1e6, -35, 1.0), (101.5e6, -42, 1.0), (162.475e6, -38, 0.9),
        (121.5e6, -45, 0.4), (156.8e6, -48, 0.3), (462.5625e6, -50, 0.35),
        (851.0125e6, -52, 0.5), (1090e6, -47, 0.6), (1575.42e6, -58, 1.0),
        (2437e6, -40, 0.8), (5180e6, -49, 0.5),
    ]

    def __init__(self, rate: float):
        self.rate, self.fmin, self.fmax = rate, 1e6, 6e9
        self.cf = 0.0
        self.rng = np.random.default_rng()

    def tune(self, freq: float):
        self.cf = freq

    def capture(self, n: int) -> np.ndarray:
        sigma = 1e-2 / np.sqrt(2)  # about -70 dBFS per bin at 2048-pt FFT
        x = (self.rng.normal(0, sigma, n) + 1j * self.rng.normal(0, sigma, n)).astype(np.complex64)
        t = np.arange(n) / self.rate
        for f, db, duty in self.TONES:
            if abs(f - self.cf) < self.rate * 0.45 and self.rng.random() < duty:
                x += (10 ** (db / 20)) * np.exp(2j * np.pi * (f - self.cf) * t)
        time.sleep(n / self.rate * 0.2)
        return x

    def close(self):
        pass


# --------------------------------------------------------------------------
# DSP
# --------------------------------------------------------------------------
def psd_dbfs(iq: np.ndarray, nfft: int, window: np.ndarray) -> np.ndarray:
    """Averaged power spectrum in dBFS per bin (full-scale tone peaks at 0 dBFS)."""
    frames = len(iq) // nfft
    x = iq[: frames * nfft].reshape(frames, nfft) * window
    spec = np.fft.fftshift(np.fft.fft(x, axis=1), axes=1)
    p = (np.abs(spec) ** 2).mean(axis=0) / (window.sum() ** 2)
    return 10.0 * np.log10(p + 1e-20)


def scan_band(rx, band: Band, args, stop: Optional[threading.Event] = None) -> np.ndarray:
    """Return a spectrum array (dBFS, NaN where not measured) spanning the band."""
    binhz = rx.rate / args.fft
    n = int(np.ceil((band.hi - band.lo) / binhz)) + 1
    spec = np.full(n, np.nan)
    usable = rx.rate * 0.8                      # drop filter roll-off at the edges
    step = usable / 2                           # 50% overlap so the DC spike is covered by neighbours
    mid = (band.lo + band.hi) / 2
    if band.hi - band.lo <= usable:
        centers = [mid - usable * 0.25, mid + usable * 0.25] if args.dc_bins else [mid]
    else:
        centers = list(np.arange(band.lo + usable / 2, band.hi - usable / 2 + step, step))
        if centers[-1] + usable / 2 < band.hi:
            centers.append(band.hi - usable / 2)
    window = np.hanning(args.fft)
    n_samples = int(rx.rate * args.dwell)
    n_samples = max(n_samples, args.fft * 4)
    offsets = (np.arange(args.fft) - args.fft / 2) * binhz

    for c in centers:
        if stop is not None and stop.is_set():
            break
        rx.tune(c)
        rx.capture(args.fft * 4)                # discard samples while the tuner settles
        vals = psd_dbfs(rx.capture(n_samples), args.fft, window) + args.cal_offset
        freqs = c + offsets
        keep = np.abs(offsets) <= usable / 2
        if args.dc_bins:
            keep &= np.abs(offsets) > args.dc_bins * binhz
        keep &= (freqs >= band.lo) & (freqs <= band.hi)
        idx = np.rint((freqs[keep] - band.lo) / binhz).astype(int)
        idx = np.clip(idx, 0, n - 1)
        spec[idx] = np.fmax(spec[idx], vals[keep])
    return spec


def detect(spec: np.ndarray, band: Band, binhz: float, threshold: float, merge_gap: int = 2):
    """Cluster adjacent above-threshold bins into signals: (freq_hz, peak_db, bw_hz)."""
    idx = np.flatnonzero(np.nan_to_num(spec, nan=-999.0) > threshold)
    if idx.size == 0:
        return []
    out = []
    for g in np.split(idx, np.flatnonzero(np.diff(idx) > merge_gap) + 1):
        k = g[np.argmax(spec[g])]
        out.append((band.lo + k * binhz, float(spec[k]), (g[-1] - g[0] + 1) * binhz))
    return out


# --------------------------------------------------------------------------
# Signal tracking
# --------------------------------------------------------------------------
def estimate_floor(spec: np.ndarray, percentile: float) -> Optional[float]:
    """Noise-floor estimate for one band: a low percentile of the measured bins.

    A low percentile (default 25th) stays on the noise even when a good share of
    the band is occupied by signals, which would drag a plain median upward.
    """
    v = spec[np.isfinite(spec)]
    if v.size == 0:
        return None
    return float(np.percentile(v, percentile))


class Signal:
    def __init__(self, freq, band, db, bw, now, thr):
        self.freq, self.band, self.db, self.max_db, self.bw = freq, band, db, db, bw
        self.thr = thr                      # detection threshold in effect for this signal
        self.first_seen = self.last_seen = now
        self.hits = 1

    def update(self, freq, db, bw, now, thr):
        self.freq, self.db, self.bw, self.last_seen, self.thr = freq, db, bw, now, thr
        self.max_db = max(self.max_db, db)
        self.hits += 1


class Tracker:
    def __init__(self, resolution_hz: float):
        self.res = resolution_hz
        self.sigs: Dict[tuple, Signal] = {}
        self.band_floor: Dict[float, tuple] = {}   # band.lo -> (noise floor, threshold)

    def add(self, band, freq, db, bw, now, thr):
        key = (band.lo, int(round(freq / self.res)))
        s = self.sigs.get(key)
        if s:
            s.update(freq, db, bw, now, thr)
        else:
            self.sigs[key] = Signal(freq, band, db, bw, now, thr)


# --------------------------------------------------------------------------
# Text report (written continuously while the scan runs)
# --------------------------------------------------------------------------
class TextReport:
    """Plain-text results file, flushed line by line so it is readable mid-scan."""

    def __init__(self, path: str, args, bands: List[Band], rx):
        self.path = path
        self.fh = open(path, "a", buffering=1)
        w = self.fh.write
        w("=" * 100 + "\n")
        w(f"SDR FCC band scan - started {dt.datetime.now():%Y-%m-%d %H:%M:%S}\n")
        dev = "simulated" if args.simulate else (args.device or "first available device")
        gain = "auto" if args.gain is None else f"{args.gain:g} dB"
        w(f"Device: {dev} | sample rate {rx.rate / 1e6:g} MS/s | FFT {args.fft} | "
          f"dwell {args.dwell:g} s | gain {gain}\n")
        if args.noise_floor is None:
            w(f"Noise floor: AUTO - per band, {args.floor_percentile:g}th percentile of the "
              f"spectrum + {args.floor_margin:g} dB margin\n")
        else:
            w(f"Noise floor: fixed threshold {args.noise_floor:g} dBFS\n")
        w(f"Bands: {len(bands)} ({bands[0].lo / 1e6:g}-{bands[-1].hi / 1e6:g} MHz)\n")
        w("=" * 100 + "\n")

    def band(self, now, band: Band, floor, thr, n_found: int):
        f = f"{floor:.1f}" if floor is not None else "n/a"
        self.fh.write(f"{now:%H:%M:%S}  [band] {band.name:<24} {band.lo / 1e6:>9.3f}-"
                      f"{band.hi / 1e6:<9.3f} MHz  floor {f:>7} dBFS  threshold {thr:>7.1f} dBFS  "
                      f"signals {n_found}\n")

    def detection(self, now, band: Band, f, db, bw, thr):
        self.fh.write(f"{now:%H:%M:%S}  [SIGNAL] {f / 1e6:>12.4f} MHz  {db:>7.1f} dBFS "
                      f"(+{db - thr:.1f} dB)  BW {bw / 1e3:>7.1f} kHz  "
                      f"{band.name} - {band.use}\n")

    def sweep_summary(self, tracker: Tracker, sweep: int):
        now = dt.datetime.now()
        sigs = sorted(tracker.sigs.values(), key=lambda s: s.freq)
        w = self.fh.write
        w("\n" + "-" * 100 + "\n")
        w(f"Sweep {sweep} complete at {now:%H:%M:%S} - {len(sigs)} signal(s) tracked so far\n")
        if sigs:
            w(f"{'Freq (MHz)':>12}  {'dBFS':>6}  {'Max':>6}  {'BW kHz':>7}  {'Hits':>4}  "
              f"{'First seen':<19}  {'Last seen':<19}  Band / allocation\n")
            for s in sigs:
                w(f"{s.freq / 1e6:>12.4f}  {s.db:>6.1f}  {s.max_db:>6.1f}  {s.bw / 1e3:>7.1f}  "
                  f"{s.hits:>4}  {s.first_seen:%Y-%m-%d %H:%M:%S}  {s.last_seen:%Y-%m-%d %H:%M:%S}  "
                  f"{s.band.name} - {s.band.use}\n")
        w("-" * 100 + "\n\n")

    def close(self, sweeps: int):
        self.fh.write(f"Scan ended {dt.datetime.now():%Y-%m-%d %H:%M:%S} after {sweeps} sweep(s)\n")
        self.fh.close()


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
def fmt_ago(sec: float) -> str:
    sec = int(sec)
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m {sec % 60:02d}s ago"
    return f"{sec // 3600}h {(sec % 3600) // 60:02d}m ago"


def make_panel(tracker: Tracker, band: Band, bs: List[Signal], args, now, scanning: bool) -> Panel:
    t = Table(box=None, expand=True, padding=(0, 1), show_header=True, header_style="dim")
    t.add_column("Freq (MHz)", justify="right", no_wrap=True)
    t.add_column("Level above floor", ratio=1, no_wrap=True)
    t.add_column("dBFS", justify="right", no_wrap=True)
    t.add_column("BW kHz", justify="right", no_wrap=True)
    t.add_column("Last seen", no_wrap=True)
    for s in bs:
        age = (now - s.last_seen).total_seconds()
        color = "green" if age < args.live_secs else "yellow" if age < 300 else "grey50"
        frac = min(max((s.db - s.thr) / args.bar_span, 0.02), 1.0)
        bar = Text("█" * max(1, int(frac * args.bar_width)), style=color)
        t.add_row(f"{s.freq / 1e6:,.4f}", bar, f"{s.db:.1f}", f"{s.bw / 1e3:.1f}",
                  Text(f"{s.last_seen:%H:%M:%S} ({fmt_ago(age)})", style=color))
    floor, thr = tracker.band_floor.get(band.lo, (None, None))
    floor_txt = ""
    if thr is not None:
        floor_txt = (f"  [dim]noise {floor:.1f} / threshold {thr:.1f} dBFS[/]"
                     if floor is not None else f"  [dim]threshold {thr:.1f} dBFS[/]")
    mark = "[bold cyan]» [/]" if scanning else ""
    return Panel(
        t, border_style="cyan", padding=(0, 1),
        title=f"{mark}[bold]{band.name}[/]  {band.lo / 1e6:g}-{band.hi / 1e6:g} MHz{floor_txt}",
        subtitle=band.use, title_align="left", subtitle_align="left")


def build_blocks(tracker: Tracker, bands: List[Band], args, show_all: bool, scanning_lo):
    """One renderable per band: a panel if it has signals, else (optionally) a one-line row."""
    now = dt.datetime.now()
    by_band: Dict[float, List[Signal]] = {}
    for s in tracker.sigs.values():
        by_band.setdefault(s.band.lo, []).append(s)
    blocks = []
    for band in bands:
        bs = sorted(by_band.get(band.lo, []), key=lambda s: s.freq)
        floor, thr = tracker.band_floor.get(band.lo, (None, None))
        scanning = band.lo == scanning_lo
        if bs:
            blocks.append((band.lo, make_panel(tracker, band, bs, args, now, scanning and show_all)))
        elif show_all:
            rng = f"{band.lo / 1e6:g}-{band.hi / 1e6:g} MHz"
            if thr is None:
                if not scanning:
                    continue                           # not scanned yet
                txt, style = f"  » {band.name}  {rng}  - scanning...", "bold cyan"
            else:
                fl = f"noise {floor:.1f} / " if floor is not None else ""
                txt = (f"  {'»' if scanning else '○'} {band.name}  {rng}  {fl}threshold "
                       f"{thr:.1f} dBFS  - no signals   [{band.use}]")
                style = "bold cyan" if scanning else "dim"
            blocks.append((band.lo, Text(txt, style=style, no_wrap=True, overflow="ellipsis")))
    return blocks


def make_head(tracker: Tracker, args, st) -> Panel:
    head = Text()
    head.append("SDR FCC Band Scanner", style="bold")
    floor_txt = (f"auto floor (+{args.floor_margin:g} dB)" if args.noise_floor is None
                 else f"floor {args.noise_floor:g} dBFS")
    head.append(f"   sweep {st.sweep}   {floor_txt}   signals {len(tracker.sigs)}\n")
    head.append(st.status, style="cyan")
    out_path = getattr(args, "out_path", None)
    if out_path:
        head.append(f"\nSaving results to {out_path}", style="dim")
    return Panel(head, border_style="blue", padding=(0, 1))


# --------------------------------------------------------------------------
# Interactive scrolling UI
# --------------------------------------------------------------------------
class ScanState:
    """State shared between the scanning thread and the UI thread."""

    def __init__(self):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.done = threading.Event()
        self.status = "Starting..."
        self.sweep = 0
        self.version = 0            # bumped whenever the scan changes what should be drawn
        self.scanning_lo = None     # band currently being scanned (by band.lo)
        self.last_hit_lo = None     # band of the most recent detection
        self.error: Optional[BaseException] = None
        # view state (UI thread only)
        self.follow = True
        self.show_all = False
        self.anchor_lo = None       # the band at the top of the view ...
        self.anchor_line = 0        # ... and how many lines into it, so growth above doesn't shift the view
        self.cmds: List[str] = []


_KEYSEQ = {
    b"\x1b[A": "up", b"\x1bOA": "up", b"\x1b[B": "down", b"\x1bOB": "down",
    b"\x1b[5~": "pgup", b"\x1b[6~": "pgdn",
    b"\x1b[H": "home", b"\x1bOH": "home", b"\x1b[1~": "home",
    b"\x1b[F": "end", b"\x1bOF": "end", b"\x1b[4~": "end",
    b"\x1b[Z": "shift_tab",
}
KEYCMD = {
    "up": "up", "k": "up", "down": "down", "j": "down",
    "pgup": "pgup", "b": "pgup", "pgdn": "pgdn", " ": "pgdn",
    "home": "home", "g": "home", "end": "end", "G": "end",
    "n": "next", "\t": "next", "p": "prev", "shift_tab": "prev",
}


def parse_keys(data: bytes) -> List[str]:
    keys, i = [], 0
    while i < len(data):
        if data[i] == 0x1B:
            for n in (4, 3):
                seq = data[i:i + n]
                if seq in _KEYSEQ:
                    keys.append(_KEYSEQ[seq])
                    i += n
                    break
            else:
                i += 1                      # lone/unknown escape: ignore
        else:
            keys.append(chr(data[i]))
            i += 1
    return keys


class Keyboard:
    """Non-blocking single-key input (Linux/macOS). `ok` is False where unsupported."""

    def __init__(self):
        self.ok = False

    def __enter__(self):
        try:
            import termios
            import tty
            if sys.stdin.isatty():
                self._termios = termios
                self.fd = sys.stdin.fileno()
                self._old = termios.tcgetattr(self.fd)
                tty.setcbreak(self.fd)      # keeps Ctrl-C working
                self.ok = True
        except Exception:
            self.ok = False
        return self

    def __exit__(self, *exc):
        if self.ok:
            self._termios.tcsetattr(self.fd, self._termios.TCSADRAIN, self._old)
        return False

    def poll(self, timeout: float) -> List[str]:
        if not self.ok:
            time.sleep(timeout)
            return []
        import select
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return []
        return parse_keys(os.read(self.fd, 64))


class Frame:
    """A pre-rendered screenful of lines (each a list of Segments)."""

    def __init__(self, lines):
        self.lines = lines

    def __rich_console__(self, console, options):
        last = len(self.lines) - 1
        for i, line in enumerate(self.lines):
            yield from line
            if i < last:
                yield Segment.line()


def compose_frame(console, st: ScanState, tracker: Tracker, bands, args, cache, kb_ok: bool):
    w, h = console.size
    opts = console.options.update(width=w, height=None)
    stale = (cache["w"] != w or cache["ver"] != st.version or cache["all"] != st.show_all
             or time.monotonic() - cache["t"] > 1.0)
    if stale:
        with st.lock:
            blocks = build_blocks(tracker, bands, args, st.show_all, st.scanning_lo)
            head = make_head(tracker, args, st)
            ver = st.version
        if not blocks:
            blocks = [(None, Text("  No signals above the threshold yet...", style="dim"))]
        lines, starts, order = [], {}, []
        for lo, r in blocks:
            ls = console.render_lines(r, opts, pad=False)
            starts[lo] = len(lines)
            order.append(lo)
            lines.extend(ls)
        cache.update(w=w, ver=ver, all=st.show_all, t=time.monotonic(), lines=lines,
                     starts=starts, order=order, head=console.render_lines(head, opts, pad=False))
    lines, starts, order, head_lines = cache["lines"], cache["starts"], cache["order"], cache["head"]

    body_h = max(1, h - len(head_lines) - 1)
    total = len(lines)
    max_off = max(0, total - body_h)

    def start_of(lo):
        if lo in starts:
            return starts[lo]
        if lo is not None:
            later = [x for x in order if x is not None and x >= lo]
            if later:
                return starts[later[0]]
        return 0

    if st.follow:
        tgt = st.scanning_lo if st.show_all else st.last_hit_lo
        if tgt in starts:
            st.anchor_lo, st.anchor_line = tgt, 0
    off = start_of(st.anchor_lo) + st.anchor_line if st.anchor_lo is not None else 0
    off = max(0, min(off, max_off))

    page = max(1, body_h - 1)
    for cmd in st.cmds:
        st.follow = False
        if cmd == "up":
            off -= 1
        elif cmd == "down":
            off += 1
        elif cmd == "pgup":
            off -= page
        elif cmd == "pgdn":
            off += page
        elif cmd == "home":
            off = 0
        elif cmd == "end":
            off = max_off
        elif cmd == "next":
            nxt = [s for s in sorted(starts.values()) if s > off]
            off = nxt[0] if nxt else max_off
        elif cmd == "prev":
            prv = [s for s in sorted(starts.values()) if s < off]
            off = prv[-1] if prv else 0
        off = max(0, min(off, max_off))
    st.cmds.clear()

    best = None                                  # re-anchor on the band now at the top
    for lo in order:
        if starts[lo] <= off:
            best = lo
    st.anchor_lo = best
    st.anchor_line = off - starts[best] if best is not None else 0

    body = lines[off:off + body_h]
    body = body + [[] for _ in range(body_h - len(body))]
    if kb_ok:
        keys = ("↑↓ PgUp/PgDn Home/End scroll · n/p band · "
                f"f follow:{'ON' if st.follow else 'off'} · "
                f"a all:{'ON' if st.show_all else 'off'} · q quit")
    else:
        keys = "keyboard scrolling unavailable on this terminal · Ctrl-C to quit"
    pos = f"{off + 1 if total else 0}-{min(total, off + body_h)}/{total}"
    footer = Text(f" {pos} │ {keys}", no_wrap=True, overflow="ellipsis")
    foot_lines = console.render_lines(footer, opts, style=Style(reverse=True))
    return Frame(head_lines + body + foot_lines[:1])


def run_ui(console, st: ScanState, tracker: Tracker, bands, args, worker: threading.Thread):
    cache = {"w": None, "t": 0.0, "ver": -1, "all": None}
    with Keyboard() as kb:
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                for k in kb.poll(0.1):
                    if k == "q":
                        return
                    if k == "f":
                        st.follow = True
                    elif k == "a":
                        st.show_all = not st.show_all
                    elif k in KEYCMD:
                        st.cmds.append(KEYCMD[k])
                if st.error is not None and not worker.is_alive():
                    return
                live.update(compose_frame(console, st, tracker, bands, args, cache, kb.ok),
                            refresh=True)


# --------------------------------------------------------------------------
# Scanning thread
# --------------------------------------------------------------------------
def scan_worker(rx, bands, args, tracker: Tracker, st: ScanState, report, log, log_fh):
    binhz = rx.rate / args.fft
    try:
        while not st.stop.is_set():
            with st.lock:
                st.sweep += 1
                sweep = st.sweep
            for i, band in enumerate(bands, 1):
                if st.stop.is_set():
                    return
                with st.lock:
                    st.scanning_lo = band.lo
                    st.status = (f"Scanning [{i}/{len(bands)}] {band.name} "
                                 f"({band.lo / 1e6:g}-{band.hi / 1e6:g} MHz)")
                    st.version += 1
                spec = scan_band(rx, band, args, st.stop)
                if st.stop.is_set():
                    return
                now = dt.datetime.now()
                floor = estimate_floor(spec, args.floor_percentile)
                if args.noise_floor is not None:
                    thr = args.noise_floor
                elif floor is not None:
                    thr = floor + args.floor_margin
                else:
                    continue                              # nothing measured in this band
                found = detect(spec, band, binhz, thr)
                with st.lock:
                    tracker.band_floor[band.lo] = (floor, thr)
                    if report:
                        report.band(now, band, floor, thr, len(found))
                    for f, db, bw in found:
                        tracker.add(band, f, db, bw, now, thr)
                        if report:
                            report.detection(now, band, f, db, bw, thr)
                        if log:
                            log.writerow([now.isoformat(timespec="seconds"), f"{f / 1e6:.4f}",
                                          f"{db:.1f}", f"{bw / 1e3:.1f}", band.name, band.use])
                            log_fh.flush()
                    if found:
                        st.last_hit_lo = band.lo
                    st.version += 1
            with st.lock:
                if report:
                    report.sweep_summary(tracker, sweep)
            if args.once:
                with st.lock:
                    st.scanning_lo = None
                    st.status = "Scan complete (--once). Review with the arrow keys, press q to exit."
                    st.version += 1
                return
    except Exception as exc:                              # surfaced by the UI thread
        st.error = exc
    finally:
        st.done.set()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="Sequential FCC-band SDR scanner with terminal graph.")
    p.add_argument("--noise-floor", type=float, default=None,
                   help="fixed detection threshold in dBFS per FFT bin; signals above it are "
                        "reported. If omitted, the noise floor is estimated automatically "
                        "for each band (see --floor-margin)")
    p.add_argument("--floor-margin", type=float, default=6.0,
                   help="auto mode: dB above the estimated noise floor a signal must reach "
                        "(default 6)")
    p.add_argument("--floor-percentile", type=float, default=25.0,
                   help="auto mode: percentile of each band's spectrum taken as the noise "
                        "floor (default 25)")
    p.add_argument("--device", default="", help='SoapySDR device args, e.g. "driver=rtlsdr"')
    p.add_argument("--rate", type=float, default=2.4e6, help="sample rate in Hz (default 2.4e6)")
    p.add_argument("--gain", type=float, default=None, help="RF gain in dB (default: auto)")
    p.add_argument("--fft", type=int, default=2048, help="FFT size (default 2048)")
    p.add_argument("--dwell", type=float, default=0.1, help="seconds captured per tile (default 0.1)")
    p.add_argument("--cal-offset", type=float, default=0.0,
                   help="dB added to every reading (use to convert dBFS to calibrated dBm)")
    p.add_argument("--dc-bins", type=int, default=4,
                   help="bins masked around tile centre to hide DC spike (0 disables)")
    p.add_argument("--resolution-khz", type=float, default=25.0,
                   help="signals closer than this are treated as the same signal (default 25)")
    p.add_argument("--min-mhz", type=float, default=None, help="skip bands below this frequency")
    p.add_argument("--max-mhz", type=float, default=None, help="skip bands above this frequency")
    p.add_argument("--only", default=None,
                   help="comma-separated substrings; scan only bands whose name matches")
    p.add_argument("--bands-file", default=None, help="CSV with columns low_mhz,high_mhz,name,use")
    p.add_argument("--once", action="store_true", help="do a single sweep, then print results and exit")
    p.add_argument("--live-secs", type=float, default=30.0,
                   help="signals seen within this many seconds are shown green (default 30)")
    p.add_argument("--bar-span", type=float, default=40.0,
                   help="dB above the floor that fills a full bar (default 40)")
    p.add_argument("--bar-width", type=int, default=40, help="max bar width in characters")
    p.add_argument("--log", default=None, help="append each detection to this CSV file")
    p.add_argument("--output", default=None,
                   help="text file for results, written while the scan runs "
                        "(default: sdr_scan_YYYYmmdd_HHMMSS.txt in the current directory)")
    p.add_argument("--no-output", action="store_true", help="do not write a results text file")
    p.add_argument("--menu", action="store_true",
                   help="show the band-selection menu even if a saved selection exists")
    p.add_argument("--config", default=None,
                   help="band-selection config file (default: sdr_scanner.conf next to this script)")
    p.add_argument("--ignore-config", action="store_true",
                   help="skip the menu and config file; scan every band that passes the filters")
    p.add_argument("--simulate", action="store_true", help="use synthetic data, no SDR needed")
    return p.parse_args()


# --------------------------------------------------------------------------
# Band selection: config file + start-up menu
# --------------------------------------------------------------------------
CONF_NAME = "sdr_scanner.conf"


def default_conf_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), CONF_NAME)


def fmt_mhz(hz: float) -> str:
    return f"{hz / 1e6:.6f}".rstrip("0").rstrip(".")


def band_key(b: Band) -> str:
    """Stable identifier for a band in the config file: '<low MHz>-<high MHz>'."""
    return f"{fmt_mhz(b.lo)}-{fmt_mhz(b.hi)}"


def load_conf(path: str):
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"), interpolation=None)
    with open(path) as fh:
        cp.read_file(fh)
    sel: Dict[str, bool] = {}
    if cp.has_section("bands"):
        for k, v in cp.items("bands"):
            sel[k.strip()] = v.strip().lower() in ("1", "yes", "true", "on")
    settings = dict(cp.items("settings")) if cp.has_section("settings") else {}
    return sel, settings


def save_conf(path: str, all_bands: List[Band], sel: Dict[str, bool], settings: Dict[str, str]) -> bool:
    meta = {band_key(b): b for b in all_bands}

    def order(k):
        try:
            return float(k.split("-")[0])
        except ValueError:
            return float("inf")

    lines = [
        f"# SDR FCC band scanner - band selection (written {dt.datetime.now():%Y-%m-%d %H:%M:%S})",
        "# Edit by hand, or re-open the menu with:  python sdr_band_scanner.py --menu",
        '# Each line under [bands] is "<low MHz>-<high MHz> = on|off". Bands missing from',
        "# this file default to on.",
        "",
        "[settings]",
        "# first-run = show the menu only when this file does not exist; always = every start",
        f"show_menu = {settings.get('show_menu', 'first-run')}",
        "",
        "[bands]",
    ]
    for k in sorted(sel, key=order):
        b = meta.get(k)
        note = f"   # {b.name} - {b.use}" if b else "   # (not in the current band list)"
        lines.append(f"{k} = {'on' if sel[k] else 'off'}{note}")
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as fh:
            fh.write("\n".join(lines) + "\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def run_menu(console, kb: Keyboard, avail, states: Dict[str, bool]) -> Optional[Dict[str, bool]]:
    """Checklist UI. Returns the chosen states, or None if the user quit."""
    states = dict(states)
    keys_all = [k for k, _, _ in avail]
    n = len(avail)
    cursor = top = 0
    msg = ""
    with Live(console=console, screen=True, auto_refresh=False) as live:
        while True:
            w, h = console.size
            body_h = max(3, h - 3)
            cursor = max(0, min(cursor, n - 1))
            if cursor < top:
                top = cursor
            elif cursor >= top + body_h:
                top = cursor - body_h + 1
            top = max(0, min(top, max(0, n - body_h)))
            on = sum(1 for k in keys_all if states[k])

            rows = []
            for i in range(top, min(n, top + body_h)):
                key, orig, _ = avail[i]
                mark = "x" if states[key] else " "
                txt = (f"{'>' if i == cursor else ' '} [{mark}] {fmt_mhz(orig.lo):>9} - "
                       f"{fmt_mhz(orig.hi):<9} MHz  {orig.name:<24} {orig.use}")
                style = "reverse" if i == cursor else ("" if states[key] else "dim")
                rows.append(Text(txt, style=style, no_wrap=True, overflow="ellipsis"))
            rows += [Text("") for _ in range(body_h - len(rows))]
            head = Text(no_wrap=True, overflow="ellipsis")
            head.append("Select the bands to scan", style="bold")
            head.append(f"   {on}/{n} selected")
            helpline = Text("↑↓ move · Space toggle · a all · c clear · i invert · PgUp/PgDn · "
                            "Enter save & start · q quit", style="dim", no_wrap=True,
                            overflow="ellipsis")
            foot = Text(f" {cursor + 1}/{n}  {msg}", style="reverse", no_wrap=True,
                        overflow="ellipsis")
            foot.pad_right(max(0, w - len(foot)))
            live.update(Group(head, helpline, *rows, foot), refresh=True)

            for k in kb.poll(0.3):
                msg = ""
                if k in ("up", "k"):
                    cursor -= 1
                elif k in ("down", "j"):
                    cursor += 1
                elif k == "pgup":
                    cursor -= body_h - 1
                elif k == "pgdn":
                    cursor += body_h - 1
                elif k in ("home", "g"):
                    cursor = 0
                elif k in ("end", "G"):
                    cursor = n - 1
                elif k == " ":
                    key = avail[max(0, min(cursor, n - 1))][0]
                    states[key] = not states[key]
                elif k == "a":
                    states = {key: True for key in keys_all}
                elif k == "c":
                    states = {key: False for key in keys_all}
                elif k == "i":
                    states = {key: not states[key] for key in keys_all}
                elif k in ("\r", "\n"):
                    if any(states.values()):
                        return states
                    msg = "Select at least one band first"
                elif k == "q":
                    return None
                cursor = max(0, min(cursor, n - 1))


def choose_bands(console, args, all_bands: List[Band], avail) -> Optional[List[Band]]:
    """Apply the saved selection (creating the conf and showing the menu on first run)."""
    if args.ignore_config:
        return [clipped for _, _, clipped in avail]

    path = args.config or default_conf_path()
    exists = os.path.exists(path)
    sel: Dict[str, bool] = {}
    settings: Dict[str, str] = {}
    if exists:
        try:
            sel, settings = load_conf(path)
        except (configparser.Error, OSError) as exc:
            console.print(f"[yellow]Could not read {path} ({exc}); a new one will be created "
                          f"(old file kept as .bak).[/]")
            try:
                os.replace(path, path + ".bak")
            except OSError:
                pass
            exists = False

    states = {key: sel.get(key, True) for key, _, _ in avail}   # unknown bands default to on
    mode = settings.get("show_menu", "first-run").strip().lower()
    want_menu = args.menu or not exists or mode == "always"
    changed = not exists

    if want_menu:
        if not console.is_terminal:
            console.print("[yellow]Not an interactive terminal - skipping the band menu.[/]")
        else:
            with Keyboard() as kb:
                if kb.ok:
                    try:
                        result = run_menu(console, kb, avail, states)
                    except KeyboardInterrupt:
                        result = None
                    if result is None:
                        return None
                    states, changed = result, True
                else:
                    console.print(f"[yellow]The interactive menu isn't supported on this terminal; "
                                  f"edit {path} by hand.[/]")

    if changed:
        merged = dict(sel)
        merged.update(states)
        for b in all_bands:
            merged.setdefault(band_key(b), True)
        if save_conf(path, all_bands, merged, settings):
            console.print(f"[dim]Band selection saved to {path}[/]")
        else:
            console.print(f"[yellow]Could not write {path}; the selection won't be remembered.[/]")
    else:
        n_on = sum(1 for v in states.values() if v)
        console.print(f"[dim]Using saved band selection from {path}: {n_on} of {len(states)} bands "
                      f"(run with --menu to change it)[/]")

    chosen = [clipped for key, _, clipped in avail if states[key]]
    if not chosen:
        console.print(f"[yellow]Every band is switched off in {path}. "
                      f"Run with --menu to choose some.[/]")
    return chosen


def main():
    args = parse_args()
    console = Console()

    all_bands = load_bands_file(args.bands_file) if args.bands_file else list(DEFAULT_BANDS)
    bands = list(all_bands)
    if args.min_mhz is not None:
        bands = [b for b in bands if b.hi >= args.min_mhz * 1e6]
    if args.max_mhz is not None:
        bands = [b for b in bands if b.lo <= args.max_mhz * 1e6]
    if args.only:
        keys = [k.strip().lower() for k in args.only.split(",")]
        bands = [b for b in bands if any(k in b.name.lower() or k in b.use.lower() for k in keys)]

    try:
        rx = SimReceiver(args.rate) if args.simulate else \
            SoapyReceiver(args.device, args.rate, args.gain)
    except Exception as exc:
        console.print(f"[red]Could not open SDR:[/] {exc}")
        return 1

    # Bands the radio can tune, clipped to its range. The config key always comes from
    # the original (unclipped) band, so saved selections survive a change of radio.
    avail, skipped = [], 0
    for b in bands:
        if b.hi < rx.fmin or b.lo > rx.fmax:
            skipped += 1
            continue
        avail.append((band_key(b), b, b._replace(lo=max(b.lo, rx.fmin), hi=min(b.hi, rx.fmax))))
    if not avail:
        console.print("[red]No bands in range of this SDR with the current filters.[/]")
        rx.close()
        return 1
    if skipped:
        console.print(f"[dim]Skipping {skipped} band(s) outside the SDR's "
                      f"{rx.fmin / 1e6:g}-{rx.fmax / 1e6:g} MHz range.[/]")

    bands = choose_bands(console, args, all_bands, avail)
    if not bands:
        console.print("[dim]No bands selected - nothing to scan.[/]")
        rx.close()
        return 0

    tracker = Tracker(args.resolution_khz * 1e3)
    log_fh = open(args.log, "a", newline="") if args.log else None
    log = csv.writer(log_fh) if log_fh else None
    report = None
    if not args.no_output:
        path = args.output or f"sdr_scan_{dt.datetime.now():%Y%m%d_%H%M%S}.txt"
        try:
            report = TextReport(path, args, bands, rx)
        except OSError as exc:
            console.print(f"[red]Could not open output file {path}:[/] {exc}")
            rx.close()
            return 1
    args.out_path = report.path if report else None

    st = ScanState()
    worker = threading.Thread(target=scan_worker, daemon=True,
                              args=(rx, bands, args, tracker, st, report, log, log_fh))
    try:
        if console.is_terminal:
            worker.start()
            run_ui(console, st, tracker, bands, args, worker)
        else:                                   # piped/redirected: no UI, just scan
            scan_worker(rx, bands, args, tracker, st, report, log, log_fh)
    except KeyboardInterrupt:
        pass
    finally:
        st.stop.set()
        if worker.is_alive():
            worker.join(timeout=15)
        rx.close()
        if log_fh:
            log_fh.close()
        if report:
            report.close(st.sweep)

    if st.error is not None:
        console.print(f"[red]Scan stopped by an error:[/] {st.error}")
    st.status = f"Stopped after {st.sweep} sweep(s)"
    console.print(make_head(tracker, args, st))
    blocks = build_blocks(tracker, bands, args, False, None)
    for _, renderable in blocks:
        console.print(renderable)
    if not blocks:
        console.print("[dim]No signals were found above the threshold.[/]")
    if report:
        console.print(f"Results saved to [bold]{report.path}[/]")
    return 0 if st.error is None else 1


if __name__ == "__main__":
    sys.exit(main())
