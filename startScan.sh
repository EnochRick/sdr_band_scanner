#!/bin/bash

source venv/bin/activate     # use your venv's actual path

python3 sdr_band_scanner_v5.py --device driver=rtlsdr --gain 30 
