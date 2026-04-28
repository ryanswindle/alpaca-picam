from ctypes import (
    POINTER,
    byref,
    c_char_p,
    c_double,
    c_int,
    c_int64,
    c_uint16,
    c_uint32,
    c_void_p,
    cast,
    memmove,
    pointer,
    sizeof,
)
from datetime import datetime, timezone
from enum import IntEnum
from threading import Event, Lock, Thread
from typing import Dict, List, Optional

from astropy.time import Time
import astropy.units as u
import numpy as np
import pandas as pd
import time

from config import DeviceConfig
from log import get_logger
from picam import (
    PicamAvailableData,
    PicamAcquisitionStatus,
    PicamCameraID,
    PicamConstraintCategory,
    PicamError,
    PicamParameter,
    PicamCollectionConstraint,
    PicamRangeConstraint,
    PicamRoi,
    PicamRois,
    PicamRoisConstraint,
    PicamTimeStampsMask,
    load_picam_library,
    picam_call,
)


logger = get_logger()


class CameraState(IntEnum):
    IDLE = 0
    WAITING = 1
    EXPOSING = 2
    READING = 3
    DOWNLOADING = 4
    ERROR = 5


class SensorType(IntEnum):
    MONOCHROME = 0
    COLOR = 1
    RGGB = 2
    CMYG = 3
    CMYG2 = 4
    LRGB = 5


class ShutterState(IntEnum):
    OPEN = 0
    CLOSED = 1
    OPENING = 2
    CLOSING = 3
    ERROR = 4


class CameraDevice:
    """Low-level driver for the Teledyne camera (PICam)."""

    def __init__(
        self,
        device_config: DeviceConfig,
        library_path: str,
        dll_directories: List[str] = None,
    ):
        self._lock = Lock()
        self._config = device_config
        self._library_path = library_path
        self._dll_directories = dll_directories or []

        self.picam = None
        self.handle = c_void_p()
        self._camera_id: Optional[PicamCameraID] = None

        self._connected = False
        self._connecting = False
        self._connect_thread: Optional[Thread] = None
        self._disconnect_thread: Optional[Thread] = None

        self._camera_state = CameraState.IDLE
        self._image_ready = False
        self._exposure_complete = Event()

        self._last_exposure_duration: float = 0.0
        self._last_exposure_start_time: str = ""
        self._exposure_thread: Optional[Thread] = None

        self._data = PicamAvailableData()
        self._picam_data = PicamAvailableData()
        self._picam_status = PicamAcquisitionStatus()

    #######################################
    # ASCOM Methods Common To All Devices #
    #######################################
    def connect(self) -> None:
        if self._connected or self._connecting:
            return
        self._connecting = True
        self._connect_thread = Thread(target=self._connect_worker, daemon=True)
        self._connect_thread.start()

    def _connect_worker(self) -> None:
        try:
            if self.picam is None:
                self.picam = load_picam_library(self._library_path, self._dll_directories)
                if self.picam is None:
                    raise RuntimeError("Failed to load PICam library")

            picam_call(
                self.picam.Picam_InitializeLibrary, operation="InitializeLibrary"
            )
            self.handle = c_void_p()

            # Check the PICam version
            major = c_int()
            minor = c_int()
            distribution = c_int()
            release = c_int()
            picam_call(
                self.picam.Picam_GetVersion,
                pointer(major),
                pointer(minor),
                pointer(distribution),
                pointer(release),
                operation="GetVersion",
            )
            logger.debug(
                f"PICam version {major.value}.{minor.value}.{distribution.value}, released {release.value}"
            )

            if self._config.demo.enable:
                ids = PicamCameraID()
                serial = (
                    self._config.serial_number.encode()
                    if self._config.serial_number
                    else b"DEMO001"
                )
                picam_call(
                    self.picam.Picam_ConnectDemoCamera,
                    c_int(self._config.demo.model),
                    serial,
                    pointer(ids),
                    operation="ConnectDemoCamera",
                )
                self._camera_id = ids
                picam_call(
                    self.picam.Picam_OpenFirstCamera,
                    byref(self.handle),
                    operation="OpenFirstCamera",
                )
            else:
                ids_ptr = POINTER(PicamCameraID)()
                count = c_int()

                discovery_timeout = 90  # seconds — generous for slow USB init
                poll_interval = 2
                elapsed = 0

                while elapsed < discovery_timeout:
                    picam_call(
                        self.picam.Picam_GetAvailableCameraIDs,
                        pointer(ids_ptr),
                        pointer(count),
                        operation="GetAvailableCameraIDs",
                    )
                    if count.value > 0:
                        break
                    logger.debug(
                        f"No cameras found yet, retrying in {poll_interval}s ({elapsed}/{discovery_timeout}s)"
                    )
                    self.picam.Picam_DestroyCameraIDs(ids_ptr)
                    ids_ptr = POINTER(PicamCameraID)()
                    count = c_int()
                    time.sleep(poll_interval)
                    elapsed += poll_interval

                if count.value == 0:
                    raise RuntimeError(
                        f"No cameras available after {discovery_timeout}s. "
                        "Check connection and ensure no other application has the camera open."
                    )

                logger.info(f"Found {count.value} cameras.")

                camera_index = 0
                if self._config.serial_number:
                    for i in range(count.value):
                        if (
                            ids_ptr[i].serial_number.decode()
                            == self._config.serial_number
                        ):
                            logger.debug(
                                f"Camera {i} S/N: {ids_ptr[i].serial_number.decode()}"
                            )
                            camera_index = i
                            break
                self._camera_id = ids_ptr[camera_index]
                picam_call(
                    self.picam.Picam_OpenCamera,
                    pointer(ids_ptr[camera_index]),
                    byref(self.handle),
                    operation="OpenCamera",
                )
                self.picam.Picam_DestroyCameraIDs(ids_ptr)

            self._query_camera_properties()
            self._set_default_parameters()
            self._connected = True
            self._camera_state = CameraState.IDLE
            self._image_ready = False
            logger.info(
                f"Connected to camera: {self._sensor_name} ({self._config.entity})"
            )
        except Exception as e:
            logger.error(f"Connection failed for {self._config.entity}: {e}")
            self._connected = False
            self._camera_state = CameraState.ERROR
            raise
        finally:
            self._connecting = False

    def _query_camera_properties(self) -> None:
        logger.info(f"Querying camera properties for {self._config.entity}")

        # Frame size
        logger.debug("querying SensorActiveWidth, SensorActiveHeight")
        try:
            width, height = c_int(), c_int()
            picam_call(
                self.picam.Picam_GetParameterIntegerValue,
                self.handle,
                c_int(PicamParameter["SensorActiveWidth"]),
                pointer(width),
                operation="SensorActiveWidth",
            )
            picam_call(
                self.picam.Picam_GetParameterIntegerValue,
                self.handle,
                c_int(PicamParameter["SensorActiveHeight"]),
                pointer(height),
                operation="SensorActiveHeight",
            )
            self._camera_x_size = width.value
            self._camera_y_size = height.value
        except PicamError:
            logger.warning("Unable to read frame width, height")
            self._camera_x_size = self._camera_y_size = None

        # Pixel size
        logger.debug("querying PixelWidth, PixelHeight")
        try:
            pixel_w, pixel_h = c_double(), c_double()
            picam_call(
                self.picam.Picam_GetParameterFloatingPointValue,
                self.handle,
                c_int(PicamParameter["PixelWidth"]),
                pointer(pixel_w),
                operation="PixelWidth",
            )
            picam_call(
                self.picam.Picam_GetParameterFloatingPointValue,
                self.handle,
                c_int(PicamParameter["PixelHeight"]),
                pointer(pixel_h),
                operation="PixelHeight",
            )
            self._pixel_size_x, self._pixel_size_y = pixel_w.value, pixel_h.value
        except PicamError:
            logger.warning("Unable to read pixel width, height")
            self._pixel_size_x = self._pixel_size_y = None

        self._sensor_name = (
            self._camera_id.sensor_name.decode("utf-8", errors="replace").strip()
            if self._camera_id
            else "Unknown"
        )

        # Exposure limits
        logger.debug("querying ExposureTimeRangeConstraint")
        try:
            constraint_ptr = POINTER(PicamRangeConstraint)()
            picam_call(
                self.picam.Picam_GetParameterRangeConstraint,
                self.handle,
                c_int(PicamParameter["ExposureTime"]),
                c_int(PicamConstraintCategory["Capable"]),
                byref(constraint_ptr),
                operation="ExposureTimeRangeConstraint",
            )
            self._exposure_min = constraint_ptr.contents.minimum / 1000.0
            self._exposure_max = constraint_ptr.contents.maximum / 1000.0
            self._exposure_resolution = constraint_ptr.contents.increment / 1000.0

            self.picam.Picam_DestroyRangeConstraints(constraint_ptr)
        except PicamError:
            logger.warning("Unable to read exposure time range")
            self._exposure_min, self._exposure_max, self._exposure_resolution = (
                0.0,
                3600.0,
                0.001,
            )

        # Binning limits
        logger.debug("querying RoisConstraint")
        try:
            constraint_ptr = POINTER(PicamRoisConstraint)()
            picam_call(
                self.picam.Picam_GetParameterRoisConstraint,
                self.handle,
                c_int(PicamParameter["Rois"]),
                c_int(PicamConstraintCategory["Capable"]),
                byref(constraint_ptr),
                operation="RoisConstraint",
            )

            x_bins = []
            if constraint_ptr and constraint_ptr.contents.x_binning_limits_count > 0:
                if constraint_ptr.contents.x_binning_limits_array:
                    x_bins = [
                        constraint_ptr.contents.x_binning_limits_array[i]
                        for i in range(constraint_ptr.contents.x_binning_limits_count)
                    ]

            if x_bins:
                self._available_binnings = sorted(x_bins)
                self._max_bin_x = self._max_bin_y = max(self._available_binnings)
            else:
                self._available_binnings = [1]
                self._max_bin_x = self._max_bin_y = 1

            if constraint_ptr:
                self.picam.Picam_DestroyRoisConstraints(constraint_ptr)
        except PicamError:
            logger.warning("Unable to read available binnings")
            self._available_binnings = [1]
            self._max_bin_x = self._max_bin_y = 1

        #
        ### Readout mode parameters — discover capabilities for logging
        #

        self._adc_analog_gains = self._get_collection_constraint(
            PicamParameter["AdcAnalogGain"], "AdcAnalogGain"
        )
        logger.debug(f"Camera reports capable AdcAnalogGains: {self._adc_analog_gains}")

        self._adc_qualities = self._get_collection_constraint(
            PicamParameter["AdcQuality"], "AdcQuality"
        )
        logger.debug(f"Camera reports capable AdcQualities: {self._adc_qualities}")

        self._adc_bit_depths = self._get_collection_constraint(
            PicamParameter["AdcBitDepth"], "AdcBitDepth"
        )
        logger.debug(f"Camera reports capable AdcBitDepths: {self._adc_bit_depths}")

        self._pixel_formats = self._get_collection_constraint(
            PicamParameter["PixelFormat"], "PixelFormat"
        )
        logger.debug(f"Camera reports capable PixelFormats: {self._pixel_formats}")

        self._readout_control_modes = self._get_collection_constraint(
            PicamParameter["ReadoutControlMode"], "ReadoutControlMode"
        )
        logger.debug(
            f"Camera reports capable ReadoutControlModes: {self._readout_control_modes}"
        )

        # Build readout modes table from config
        columns = [
            "AdcAnalogGain",
            "AdcQuality",
            "AdcBitDepth",
            "PixelFormat",
            "ReadoutControlMode",
        ]
        if self._config.readout_modes:
            rows = [mode.values for mode in self._config.readout_modes]
            self._readout_modes_table = pd.DataFrame(rows, columns=columns)
            self._readout_modes = [mode.label for mode in self._config.readout_modes]
        else:
            # Fallback: single row from current camera values, build name from enums
            logger.warning(
                "No readout_modes in config — using single mode from current camera state"
            )
            current = []
            for param in columns:
                val = c_int()
                picam_call(
                    self.picam.Picam_GetParameterIntegerValue,
                    self.handle,
                    c_int(PicamParameter[param]),
                    pointer(val),
                    operation=f"Get_{param}",
                )
                current.append(val.value)
            self._readout_modes_table = pd.DataFrame([current], columns=columns)
            self._build_readout_mode_names()

        logger.debug(f"readout modes table for {self._config.entity}:")
        logger.debug(
            "\n{}",
            self._readout_modes_table.to_string(
                index=False,
                max_rows=20,
                max_cols=10,
                justify="left",
            ),
        )

        # Full well capacities
        self._build_full_well_capacities()

        # Timestamp resolution (ticks per second)
        try:
            ts_res = c_int64()
            picam_call(
                self.picam.Picam_GetParameterLargeIntegerValue,
                self.handle,
                c_int(PicamParameter["TimeStampResolution"]),
                pointer(ts_res),
                operation="Get_TimeStampResolution",
            )
            self._timestamp_resolution = float(ts_res.value)
            logger.debug(f"TimeStampResolution: {self._timestamp_resolution} ticks/sec")
        except PicamError:
            logger.warning(
                "Unable to read TimeStampResolution, defaulting to 1e7 (100ns ticks)"
            )
            self._timestamp_resolution = 1e7

    def _get_collection_constraint(self, parameter: int, name: str) -> Optional[list]:
        """Read all values from a PICam collection constraint."""
        logger.debug(f"querying {name} constraint")
        try:
            constraint_ptr = POINTER(PicamCollectionConstraint)()
            picam_call(
                self.picam.Picam_GetParameterCollectionConstraint,
                self.handle,
                c_int(parameter),
                c_int(PicamConstraintCategory["Capable"]),
                byref(constraint_ptr),
                operation=f"{name}CollectionConstraint",
            )
            count = constraint_ptr.contents.values_count
            values = [constraint_ptr.contents.values_array[i] for i in range(count)]

            # Free PICam-allocated memory (collection, not range!)
            self.picam.Picam_DestroyCollectionConstraints(constraint_ptr)
            return values
        except PicamError:
            logger.warning(f"Unable to read capable {name}")
            return None

    def _build_readout_mode_names(self) -> None:
        """Build list of readout mode name strings from the table."""
        from picam import PicamEnumeratedType

        self._readout_modes = []

        for idx, row in self._readout_modes_table.iterrows():
            bit_depth = int(row["AdcBitDepth"])
            gain = self._get_enum_string(
                PicamEnumeratedType["AdcAnalogGain"], row["AdcAnalogGain"]
            ).replace(" ", "_")
            quality = self._get_enum_string(
                PicamEnumeratedType["AdcQuality"], row["AdcQuality"]
            ).replace(" ", "_")
            readout = self._get_enum_string(
                PicamEnumeratedType["ReadoutControlMode"], row["ReadoutControlMode"]
            ).replace(" ", "_")

            if quality and quality != gain:
                mode_name = f"{bit_depth}bit-{gain}-{quality}-{readout}"
            else:
                mode_name = f"{bit_depth}bit-{gain}-{readout}"

            self._readout_modes.append(mode_name)

    def _get_enum_string(self, enum_type: int, value: int) -> str:
        """Get string name for a PICam enumeration value."""
        string_ptr = c_char_p()
        try:
            picam_call(
                self.picam.Picam_GetEnumerationString,
                c_int(enum_type),
                c_int(int(value)),
                byref(string_ptr),
                operation="GetEnumerationString",
            )
            result = string_ptr.value.decode() if string_ptr.value else str(value)
            self.picam.Picam_DestroyString(string_ptr)
            return result
        except PicamError:
            return str(int(value))

    def _build_full_well_capacities(self) -> None:
        """Build full well capacity lookup by AdcAnalogGain and binning."""
        fwc = self._config.full_well_capacity

        self._full_well_capacities = {
            1: {b: float(fwc.Low * b * b) for b in self._available_binnings},
            2: {b: float(fwc.Medium * b * b) for b in self._available_binnings},
            3: {b: float(fwc.High * b * b) for b in self._available_binnings},
        }

    def _set_default_parameters(self) -> None:
        logger.debug(f"setting default parameters for {self._config.entity}")
        defaults = self._config.defaults

        # Temperature
        self.set_ccd_temperature = defaults.temperature

        # Enable window heater
        try:
            picam_call(
                self.picam.Picam_SetParameterIntegerValue,
                self.handle,
                c_int(PicamParameter["EnableSensorWindowHeater"]),
                c_int(1),
                operation="Set_EnableSensorWindowHeater",
            )
        except PicamError:
            pass

        # Readout mode
        self.readout_mode = defaults.readout_mode

        # ROI (full frame)
        self._start_x = self._start_y = 0
        self._num_x, self._num_y = self._camera_x_size, self._camera_y_size
        self._bin_x = self._bin_y = (
            defaults.binning if defaults.binning in self._available_binnings else 1
        )

        # Binning
        self.bin_x = defaults.binning

        # Readout count (single frame)
        try:
            picam_call(
                self.picam.Picam_SetParameterLargeIntegerValue,
                self.handle,
                c_int(PicamParameter["ReadoutCount"]),
                c_int64(1),
                operation="Set_ReadoutCount",
            )
        except PicamError:
            pass

        # Timestamps
        try:
            ts_mask = (
                PicamTimeStampsMask["ExposureStarted"]
                | PicamTimeStampsMask["ExposureEnded"]
            )
            picam_call(
                self.picam.Picam_SetParameterIntegerValue,
                self.handle,
                c_int(PicamParameter["TimeStamps"]),
                c_int(ts_mask),
                operation="Set_TimeStamps",
            )
        except PicamError:
            pass

        self._commit_parameters()

    def _commit_parameters(self) -> None:
        with self._lock:
            # pibln is piint (4 bytes), not c_bool (1 byte)
            acquiring = c_int()
            self.picam.Picam_IsAcquisitionRunning(self.handle, pointer(acquiring))
            if acquiring.value:
                self.picam.Picam_StopAcquisition(self.handle)
            failed, failed_count = pointer(c_int()), c_int()
            picam_call(
                self.picam.Picam_CommitParameters,
                self.handle,
                pointer(failed),
                pointer(failed_count),
                operation="CommitParameters",
            )
            self.picam.Picam_DestroyParameters(failed)

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        if value and not self._connected:
            self.connect()
        elif not value and self._connected:
            self.disconnect()

    @property
    def connecting(self) -> bool:
        return self._connecting

    def disconnect(self) -> None:
        if not self._connected and not self._connecting:
            return
        self._disconnect_thread = Thread(target=self._disconnect_worker, daemon=True)
        self._disconnect_thread.start()

    def _disconnect_worker(self) -> None:
        try:
            if self._camera_state in (CameraState.EXPOSING, CameraState.READING):
                self.abort_exposure()
            if self.handle:
                self.picam.Picam_CloseCamera(self.handle)
            if self.picam:
                self.picam.Picam_UninitializeLibrary()
            self._connected = False
            self._camera_state = CameraState.IDLE
            logger.info(f"Disconnected from camera: {self._config.entity}")
        except Exception as e:
            logger.error(f"Disconnect error for {self._config.entity}: {e}")
        finally:
            self._connecting = False

    @property
    def entity(self) -> str:
        return self._config.entity

    ######################
    # ICamera properties #
    ######################
    @property
    def bin_x(self) -> int:
        self._get_roi()
        return self._bin_x

    def _get_roi(self) -> None:
        """Get current ROI from camera and convert to binned pixels."""
        try:
            rois = pointer(PicamRois())
            picam_call(
                self.picam.Picam_GetParameterRoisValue,
                self.handle,
                c_int(PicamParameter["Rois"]),
                pointer(rois),
                operation="Get_Rois",
            )
            if rois.contents.roi_count > 0:
                roi = rois.contents.roi_array[0]
                self._bin_x = roi.x_binning
                self._bin_y = roi.y_binning
                # Convert from unbinned to binned pixels
                self._start_x = roi.x // self._bin_x
                self._start_y = roi.y // self._bin_y
                self._num_x = roi.width // self._bin_x
                self._num_y = roi.height // self._bin_y
        except PicamError:
            logger.warning("Unable to get rois")
            raise

    @bin_x.setter
    def bin_x(self, value: int) -> None:
        self._set_roi(bin_x=value, bin_y=value)

    def _set_roi(
        self, start_x=None, num_x=None, bin_x=None, start_y=None, num_y=None, bin_y=None
    ) -> None:
        """
        Set ROI with proper validation and ordering.

        All start/num values are in binned pixels per ASCOM spec.
        PICam expects unbinned pixels, so we convert accordingly.
        """
        # Start with current values
        bx = bin_x if bin_x is not None else self._bin_x
        by = bin_y if bin_y is not None else self._bin_y
        sx = start_x if start_x is not None else self._start_x
        sy = start_y if start_y is not None else self._start_y
        nx = num_x if num_x is not None else self._num_x
        ny = num_y if num_y is not None else self._num_y

        # Validate binning
        if bx not in self._available_binnings:
            raise ValueError(
                f"BinX {bx} not in available binnings {self._available_binnings}"
            )
        if by not in self._available_binnings:
            raise ValueError(
                f"BinY {by} not in available binnings {self._available_binnings}"
            )

        # Max binned dimensions
        max_binned_x = self._camera_x_size // bx
        max_binned_y = self._camera_y_size // by

        # Validate and clamp start values
        if sx < 0:
            sx = 0
        if sy < 0:
            sy = 0
        if sx >= max_binned_x:
            sx = max_binned_x - 1
        if sy >= max_binned_y:
            sy = max_binned_y - 1

        # Validate and clamp num values to fit within remaining space
        max_nx = max_binned_x - sx
        max_ny = max_binned_y - sy

        if nx < 1:
            nx = 1
        if ny < 1:
            ny = 1
        if nx > max_nx:
            nx = max_nx
        if ny > max_ny:
            ny = max_ny

        # Convert to unbinned pixels for PICam
        picam_x = sx * bx
        picam_y = sy * by
        picam_width = nx * bx
        picam_height = ny * by

        # Set ROI
        try:
            roi = PicamRoi(picam_x, picam_width, bx, picam_y, picam_height, by)
            rois = PicamRois(pointer(roi), 1)
            picam_call(
                self.picam.Picam_SetParameterRoisValue,
                self.handle,
                c_int(PicamParameter["Rois"]),
                byref(rois),
                operation="SetRois",
            )
            self._commit_parameters()
        except PicamError:
            logger.warning("Unable to set rois")
            raise

        # Store binned values
        self._start_x, self._num_x, self._bin_x = sx, nx, bx
        self._start_y, self._num_y, self._bin_y = sy, ny, by

    @property
    def bin_y(self) -> int:
        self._get_roi()
        return self._bin_y

    @bin_y.setter
    def bin_y(self, value: int) -> None:
        self._set_roi(bin_x=value, bin_y=value)

    @property
    def camera_state(self) -> CameraState:
        return self._camera_state

    @property
    def camera_x_size(self) -> int:
        return self._camera_x_size

    @property
    def camera_y_size(self) -> int:
        return self._camera_y_size

    @property
    def can_abort_exposure(self) -> bool:
        return True

    @property
    def can_asymmetric_bin(self) -> bool:
        return False

    @property
    def can_fast_readout(self) -> bool:
        return False

    @property
    def can_get_cooler_power(self) -> bool:
        return False

    @property
    def can_pulse_guide(self) -> bool:
        return False

    @property
    def can_set_ccd_temperature(self) -> bool:
        return True

    @property
    def can_stop_exposure(self) -> bool:
        return False

    @property
    def ccd_temperature(self) -> float:
        try:
            temp = c_double()
            picam_call(
                self.picam.Picam_ReadParameterFloatingPointValue,
                self.handle,
                c_int(PicamParameter["SensorTemperatureReading"]),
                pointer(temp),
                operation="SensorTemperatureReading",
            )
            return temp.value
        except PicamError:
            logger.warning("Unable to read temperature")
            return 99.0

    @property
    def cooler_on(self) -> bool:
        return True

    @property
    def exposure_max(self) -> float:
        return self._exposure_max

    @property
    def exposure_min(self) -> float:
        return self._exposure_min

    @property
    def exposure_resolution(self) -> float:
        return self._exposure_resolution

    @property
    def full_well_capacity(self) -> float:
        gain = self._readout_modes_table.loc[self._readout_mode].AdcAnalogGain
        return self._full_well_capacities.get(gain, {}).get(self._bin_x, 0.0)

    @property
    def has_shutter(self) -> bool:
        return False

    @property
    def image_array(self) -> np.ndarray:
        if not self._image_ready:
            raise RuntimeError("No image ready")
        if not self._data.initial_readout or self._data.readout_count == 0:
            raise RuntimeError("No image data available")

        self._camera_state = CameraState.DOWNLOADING

        # Get actual values from camera
        readout_size = c_int()
        picam_call(
            self.picam.Picam_GetParameterIntegerValue,
            self.handle,
            c_int(PicamParameter["ReadoutStride"]),
            byref(readout_size),
            operation="Get_ReadoutStride",
        )
        pixel_format = c_int()
        picam_call(
            self.picam.Picam_GetParameterIntegerValue,
            self.handle,
            c_int(PicamParameter["PixelFormat"]),
            byref(pixel_format),
            operation="Get_PixelFormat",
        )

        # Get the frame size
        self._get_roi()
        width, height = self._num_x, self._num_y

        # Use pixel_format.value, not self._pixel_format
        dtype = np.uint32 if pixel_format.value == 2 else np.uint16
        data_type = c_uint32 if pixel_format.value == 2 else c_uint16
        bytes_per_pixel = 4 if pixel_format.value == 2 else 2

        # Get the total frame
        data_ptr = cast(
            self._data.initial_readout,
            POINTER(data_type * (readout_size.value // bytes_per_pixel)),
        )
        frame = np.frombuffer(data_ptr.contents, dtype=dtype)

        # Extract both the image and the timestamps
        img, exposure_start, exposure_end = self._parse_picam_frame(
            frame, width, height
        )
        logger.debug(
            f"got data with {img.shape[0]} rows, {img.shape[1]} cols, dtype={img.dtype}, ExposureStart={exposure_start}, ExposureEnd={exposure_end}"
        )

        # Set the exposure start time
        self._last_exposure_start_time = (
            Time(self._last_exposure_start_time, format="isot", scale="utc")
            + exposure_start * u.second
        ).isot

        # Set the exposure duration
        actual_exp = c_double()
        picam_call(
            self.picam.Picam_GetParameterFloatingPointValue,
            self.handle,
            c_int(PicamParameter["ExposureTime"]),
            pointer(actual_exp),
            operation="Get_ExposureTime",
        )
        self._last_exposure_duration = actual_exp.value / 1000.0

        self._camera_state = CameraState.IDLE
        self._image_ready = False

        # return img
        return img.astype(np.int32)

    def _parse_picam_frame(self, frame, width, height):
        """
        Works for 14/16-bit (int16) and 18-bit (int32) PICam frames.

        Frame layout (within readout):
          - pixel data:  width*height elements
          - timestamps:  two 64-bit LE integers at start of post-pixel region
                         (ExposureStarted, ExposureEnded — relative to acquisition start)
          - padding:     remainder of ReadoutStride
        """
        frame = np.asarray(frame)
        num_pixels = width * height
        pixel_bytes = num_pixels * frame.dtype.itemsize
        frame_bytes = frame.tobytes()

        if len(frame_bytes) < pixel_bytes + 16:
            raise ValueError("Frame too short to contain two timestamps")

        pixel_bytes_region = frame_bytes[:pixel_bytes]
        img = np.frombuffer(pixel_bytes_region, dtype=frame.dtype).reshape(
            (height, width)
        )

        # Timestamps are at the START of the post-pixel region
        ts_bytes = frame_bytes[pixel_bytes : pixel_bytes + 16]
        timestamps = np.frombuffer(ts_bytes, dtype="<i8")
        s1, s2 = int(timestamps[0]), int(timestamps[1])

        ts_scale = self._timestamp_resolution
        exposure_start = s1 / ts_scale
        exposure_end = s2 / ts_scale

        logger.debug(
            f"timestamps: s1={s1} s2={s2} (scale={ts_scale}) -> "
            f"start={exposure_start:.6f}s end={exposure_end:.6f}s "
            f"duration={exposure_end - exposure_start:.6f}s"
        )

        return img, exposure_start, exposure_end

    @property
    def image_ready(self) -> bool:
        return self._image_ready

    @property
    def last_exposure_duration(self) -> float:
        return self._last_exposure_duration

    @property
    def last_exposure_start_time(self) -> str:
        return self._last_exposure_start_time

    @property
    def max_adu(self) -> int:
        return int((1 << self._adc_bit_depth) - 1)

    @property
    def max_bin_x(self) -> int:
        return self._max_bin_x

    @property
    def max_bin_y(self) -> int:
        return self._max_bin_y

    @property
    def num_x(self) -> int:
        self._get_roi()
        return self._num_x

    @num_x.setter
    def num_x(self, value: int) -> None:
        self._set_roi(num_x=value)

    @property
    def num_y(self) -> int:
        self._get_roi()
        return self._num_y

    @num_y.setter
    def num_y(self, value: int) -> None:
        self._set_roi(num_y=value)

    @property
    def pixel_size_x(self) -> float:
        return self._pixel_size_x

    @property
    def pixel_size_y(self) -> float:
        return self._pixel_size_y

    @property
    def readout_mode(self) -> int:
        return self._readout_mode

    @readout_mode.setter
    def readout_mode(self, value: int) -> None:
        if self._config.demo.enable:
            self._readout_mode = self._config.defaults.readout_mode
            self._adc_bit_depth = 18
            self._pixel_format = 2
        else:
            # Set each ADC/readout parameter; skip any with -1 (n/a for this mode)
            row = self._readout_modes_table.loc[value]
            for param in [
                "AdcAnalogGain",
                "AdcQuality",
                "AdcBitDepth",
                "PixelFormat",
                "ReadoutControlMode",
            ]:
                val = int(row[param])
                if val < 0:
                    logger.debug(f"Skipping {param} (n/a for this mode)")
                    continue
                picam_call(
                    self.picam.Picam_SetParameterIntegerValue,
                    self.handle,
                    c_int(PicamParameter[param]),
                    c_int(val),
                    operation=f"Set_{param}",
                )
            self._commit_parameters()

            self._adc_bit_depth = self._readout_modes_table.loc[
                value
            ].AdcBitDepth  # for maxadu
            self._pixel_format = self._readout_modes_table.loc[
                value
            ].PixelFormat  # for imagearray
            self._readout_mode = value

        logger.info(
            f"Set the readout mode to {self._readout_modes[self._readout_mode]}"
        )

    @property
    def readout_modes(self) -> List[str]:
        return self._readout_modes

    @property
    def sensor_name(self) -> str:
        return self._sensor_name

    @property
    def sensor_type(self) -> SensorType:
        return SensorType.MONOCHROME

    @property
    def set_ccd_temperature(self) -> float:
        try:
            temp = c_double()
            picam_call(
                self.picam.Picam_GetParameterFloatingPointValue,
                self.handle,
                c_int(PicamParameter["SensorTemperatureSetPoint"]),
                pointer(temp),
                operation="Get_SensorTemperatureSetPoint",
            )
            return temp.value
        except PicamError:
            logger.warning("Unable to read temperature set point")
            return 99.0

    @set_ccd_temperature.setter
    def set_ccd_temperature(self, value: float) -> None:
        if self.can_set_ccd_temperature:
            try:
                picam_call(
                    self.picam.Picam_SetParameterFloatingPointValue,
                    self.handle,
                    c_int(PicamParameter["SensorTemperatureSetPoint"]),
                    c_double(value),
                    operation="Set_SensorTemperatureSetPoint",
                )
                self._commit_parameters()
                logger.debug(f"set ccd temperature to {value}")
            except PicamError:
                logger.warning("Unable to set ccd temperature")

    @property
    def start_x(self) -> int:
        self._get_roi()
        return self._start_x

    @start_x.setter
    def start_x(self, value: int) -> None:
        self._set_roi(start_x=value)

    @property
    def start_y(self) -> int:
        self._get_roi()
        return self._start_y

    @start_y.setter
    def start_y(self, value: int) -> None:
        self._set_roi(start_y=value)

    @property
    def timestamp(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    ###################
    # ICamera methods #
    ###################
    def start_exposure(self, duration: float, light: bool) -> None:
        if self._camera_state != CameraState.IDLE:
            raise RuntimeError("Camera is not idle")
        self._image_ready = False
        self._camera_state = CameraState.WAITING
        self._exposure_complete.clear()
        self._exposure_thread = Thread(
            target=self._exposure_worker, args=(duration, light), daemon=True
        )
        self._exposure_thread.start()

    def _exposure_worker(self, duration: float, light: bool) -> None:
        try:
            # Set the exposure time
            picam_call(
                self.picam.Picam_SetParameterFloatingPointValue,
                self.handle,
                c_int(PicamParameter["ExposureTime"]),
                c_double(duration * 1000.0),
                operation="Set_ExposureTime",
            )
            self._commit_parameters()

            self._last_exposure_start_time = Time.now().isot
            self._last_exposure_duration = duration

            picam_call(
                self.picam.Picam_StartAcquisition,
                self.handle,
                operation="StartAcquisition",
            )

            self._camera_state = CameraState.EXPOSING
            logger.debug("starting exposure")

            timeout_ms = c_int(int((duration + 60) * 1000))
            self._picam_status.running = True

            while self._picam_status.running:
                error = self.picam.Picam_WaitForAcquisitionUpdate(
                    self.handle,
                    timeout_ms,
                    pointer(self._picam_data),
                    pointer(self._picam_status),
                )
                if error != 0 and error != 32:
                    raise PicamError(error, "WaitForAcquisitionUpdate")
                if self._picam_data.readout_count > 0:
                    self._exposure_complete.set()
                    self._camera_state = CameraState.READING
                    memmove(
                        byref(self._data),
                        byref(self._picam_data),
                        sizeof(PicamAvailableData),
                    )
                time.sleep(0.1)
            logger.debug(f"exposure complete")

            self._image_ready = True
        except Exception as e:
            logger.error(f"Exposure failed: {e}")
            self._camera_state = CameraState.ERROR
            self._image_ready = False

    def abort_exposure(self) -> None:
        if self._camera_state in (
            CameraState.EXPOSING,
            CameraState.READING,
            CameraState.WAITING,
        ):
            try:
                self.picam.Picam_StopAcquisition(self.handle)
            except Exception:
                logger.warning("Unable to abort exposure")
                pass
            self._camera_state = CameraState.IDLE
            self._image_ready = False
            self._exposure_complete.set()
