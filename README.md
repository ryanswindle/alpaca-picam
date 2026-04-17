# ASCOM Alpaca Server for Teledyne cameras (PICam)

A FastAPI-based server, implementing the ASCOM **ICameraV4** interface. Communication is via published PICam library,
which has been tested up to version 5.6.13.2512.

---

## Implemented ICameraV4 capabilities as of this driver version

| Capability           | Supported |
|----------------------|-----------|
| BayerOffsetX         | ✘         |
| BayerOffsetY         | ✘         |
| BinX                 | ✔         |
| BinY                 | ✔         |
| CameraState          | ✔         |
| CameraXSize          | ✔         |
| CameraYSize          | ✔         |
| CanAbortExposure     | ✔         |
| CanAsymmetricBin     | ✘         |
| CanFastReadout       | ✘         |
| CanGetCoolerPower    | ✘         |
| CanPulseGuide        | ✘         |
| CanSetCCDTemperature | ✔         |
| CanStopExposure      | ✘         |
| CCDTemperature       | ✔         |
| CoolerOn             | ✘         |
| CoolerPower          | ✘         |
| ElectronsPerADU      | ✘         |
| ExposureMax          | ✔         |
| ExposureMin          | ✔         |
| ExposureResolution   | ✔         |
| FastReadout          | ✘         |
| FullWellCapacity     | ✔         |
| Gain                 | ✘         |
| GainMax              | ✘         |
| GainMin              | ✘         |
| Gains                | ✔         |
| HasShutter           | ✘         |
| HeatSinkTemperature  | ✘         |
| ImageArray           | ✔         |
| ImageReady           | ✔         |
| IsPulseGuiding       | ✘         |
| LastExposureDuration | ✔         |
| MaxADU               | ✔         |
| MaxBinX              | ✔         |
| MaxBinY              | ✔         |
| NumX                 | ✔         |
| NumY                 | ✔         |
| Offset               | ✘         |
| OffsetMax            | ✘         |
| OffsetMin            | ✘         |
| Offsets              | ✘         |
| PercentCompleted     | ✘         |
| PixelSizeX           | ✔         |
| PixelSizeY           | ✔         |
| ReadoutMode          | ✔         |
| ReadoutModes         | ✔         |
| SensorName           | ✔         |
| SensorType           | ✔         |
| SetCCDTemperature    | ✔         |
| StartX               | ✔         |
| StartY               | ✔         |
| SubExposureDuration  | ✘         |
| AbortExposure        | ✔         |
| PulseGuide           | ✘         |
| StartExposure        | ✔         |
| StopExposure         | ✘         |

Tested on the Teledyne Cosmos-8k, using software timestamps.

---

## Architecture

| File               | Purpose                                     |
|--------------------|---------------------------------------------|
| `main.py`          | FastAPI app, lifespan, router wiring        |
| `config.py`        | Pydantic config models, YAML loader         |
| `config.yaml`      | User-editable configuration                 |
| `camera.py`        | FastAPI router – ICameraV4 endpoints        |
| `camera_device.py` | Low-level PICam driver                      |
| `picam.py        ` | Wrappers to PICam library                   |
| `management.py`    | `/management` Alpaca management endpoints   |
| `setup.py`         | `/setup` HTML stub pages                    |
| `discovery.py`     | UDP Alpaca discovery responder (port 32227) |
| `responses.py`     | Pydantic response models                    |
| `exceptions.py`    | ASCOM Alpaca error classes                  |
| `shr.py`           | Shared FastAPI dependencies / helpers       |
| `log.py`           | Loguru config + stdlib intercept handler    |
| `test.py`          | Quick smoke-test script                     |
| `requirements.txt` | Python package dependencies                 |
| `Dockerfile`       | Container build                             |

---

## Configuration

Edit `config.yaml` to match your camera setup. Example settings:

- `dll_directories`:
  - "C:\\Program Files\\Princeton Instruments\\PICam\\Runtime"
  - "C:\\Program Files\\Common Files\\Princeton Instruments\\Picam\\Runtime"
  - "C:\\Program Files\\Common Files\\Pleora\\eBUS SDK"
- `library`: Path to `Picam.dll`
- `devices[].defaults`: Default temperature, readout mode, binning, gain, offset, USB traffic
- `full_well_capacity`: No way to query via PICam, but could be useful parameter for Alpaca.

Camera properties (sensor size, pixel size, gain/offset ranges, exposure limits) are
**queried from the SDK at connection time** — no hardcoding required. However, readout modes
are currently defined via config because of the unique combinations of, for example, AdcAnalogGain,
AdcQuality, etc that exist and are only discoverable via iterative `PicamCommit` pushes. This may
be fixed in a future version of this Alpaca server.

Multiple Teledyne cameras can be registered by adding further entries under
`devices:` with distinct `device_number` values.

## Quick start

```bash
pip install -r requirements.txt
python main.py
```

The server starts on `0.0.0.0:5000` by default (configurable in `config.yaml`).

---

## Smoke test

```bash
# Requires hardware connected, i.e. will operate camera
python test.py
```

---

## Docker

```bash
docker build -t alpaca-picam .
docker run -d --name alpaca-picam \
    -v ./config.yaml:/alpyca/config.yaml:ro \
    --privileged -v /dev/bus/usb:/dev/bus/usb \
    -v "C:\\Program Files\\Princeton Instruments\\PICam\\Runtime":/picam/runtime:ro \
    -v "C:\\Program Files\\Common Files\\Princeton Instruments\\Picam\\Runtime":/picam/common-runtime:ro \
    -v "C:\\Program Files\\Common Files\\Pleora\\eBUS SDK":/pleora/ebus:ro \
    -v "C:\\Program Files\\Princeton Instruments\\PICam\\Runtime\\Picam.dll":/picam/runtime/Picam.dll:ro \
    --network host \
    --restart unless-stopped \
    alpaca-picam
docker logs -f alpaca-picam
```
