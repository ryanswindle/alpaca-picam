# -*- coding: utf-8 -*-
"""PICam Library Interface - ctypes definitions for Teledyne Princeton Instruments cameras."""

import os
from ctypes import CDLL, POINTER, Structure, c_bool, c_char, c_char_p, c_double, c_int, c_int16, c_int64, c_void_p
import sys
from typing import List, Optional, Union

from log import get_logger

logger = get_logger()

IS_WINDOWS = sys.platform.startswith('win')
IS_LINUX = sys.platform.startswith('linux')

# Types and Constraints
PicamEnumeratedType = {"AdcAnalogGain": 7, "AdcQuality": 8, "PixelFormat": 12, "ReadoutControlMode": 13}
PicamValueType = {"Integer": 1, "FloatingPoint": 2, "Boolean": 3, "Enumeration": 4, "Rois": 5, "LargeInteger": 6, "Pulse": 7, "Modulations": 8}
PicamConstraintCategory = {"Capable": 1, "Required": 2, "Recommended": 3}
PicamConstraintType = {"None": 1, "Range": 2, "Collection": 3, "Rois": 4, "Pulse": 5, "Modulations": 6}


def PI_V(value_type: str, constraint_type: str, n: int) -> int:
    return (PicamConstraintType[constraint_type] << 24) + (PicamValueType[value_type] << 16) + n


# Parameters
PicamParameter = {
    "ExposureTime": PI_V("FloatingPoint", "Range", 23),
    "ShutterTimingMode": PI_V("Enumeration", "Collection", 24),
    "ShutterOpeningDelay": PI_V("FloatingPoint", "Range", 46),
    "ShutterClosingDelay": PI_V("FloatingPoint", "Range", 25),
    "AdcSpeed": PI_V("FloatingPoint", "Collection", 33),
    "AdcBitDepth": PI_V("Integer", "Collection", 34),
    "AdcAnalogGain": PI_V("Enumeration", "Collection", 35),
    "AdcQuality": PI_V("Enumeration", "Collection", 36),
    "AdcEMGain": PI_V("Integer", "Range", 53),
    "TriggerSource": PI_V("Enumeration", "Collection", 79),
    "TriggerResponse": PI_V("Enumeration", "Collection", 30),
    "TriggerDetermination": PI_V("Enumeration", "Collection", 31),
    "TriggerFrequency": PI_V("FloatingPoint", "Range", 80),
    "OutputSignal": PI_V("Enumeration", "Collection", 32),
    "InvertOutputSignal": PI_V("Boolean", "Collection", 52),
    "ReadoutControlMode": PI_V("Enumeration", "Collection", 26),
    "ReadoutTimeCalculation": PI_V("FloatingPoint", "None", 27),
    "ReadoutPortCount": PI_V("Integer", "Collection", 28),
    "ReadoutOrientation": PI_V("Enumeration", "None", 54),
    "VerticalShiftRate": PI_V("FloatingPoint", "Collection", 13),
    "Rois": PI_V("Rois", "Rois", 37),
    "NormalizeOrientation": PI_V("Boolean", "Collection", 39),
    "ReadoutCount": PI_V("LargeInteger", "Range", 40),
    "ReadoutStride": PI_V("Integer", "None", 45),
    "PixelFormat": PI_V("Enumeration", "Collection", 41),
    "FrameSize": PI_V("Integer", "None", 42),
    "FrameStride": PI_V("Integer", "None", 43),
    "FramesPerReadout": PI_V("Integer", "None", 44),
    "PixelBitDepth": PI_V("Integer", "None", 48),
    "SensorType": PI_V("Enumeration", "None", 57),
    "CcdCharacteristics": PI_V("Enumeration", "None", 58),
    "SensorActiveWidth": PI_V("Integer", "None", 59),
    "SensorActiveHeight": PI_V("Integer", "None", 60),
    "SensorActiveLeftMargin": PI_V("Integer", "None", 61),
    "SensorActiveTopMargin": PI_V("Integer", "None", 62),
    "SensorActiveRightMargin": PI_V("Integer", "None", 63),
    "SensorActiveBottomMargin": PI_V("Integer", "None", 64),
    "PixelWidth": PI_V("FloatingPoint", "None", 9),
    "PixelHeight": PI_V("FloatingPoint", "None", 10),
    "PixelGapWidth": PI_V("FloatingPoint", "None", 11),
    "PixelGapHeight": PI_V("FloatingPoint", "None", 12),
    "TimeStamps": PI_V("Enumeration", "Collection", 68),
    "TimeStampResolution": PI_V("LargeInteger", "Collection", 69),
    "TimeStampBitDepth": PI_V("Integer", "Collection", 70),
    "SensorTemperatureSetPoint": PI_V("FloatingPoint", "Range", 14),
    "SensorTemperatureReading": PI_V("FloatingPoint", "None", 15),
    "SensorTemperatureStatus": PI_V("Enumeration", "None", 16),
    "DisableCoolingFan": PI_V("Boolean", "Collection", 29),
    "EnableSensorWindowHeater": PI_V("Boolean", "Collection", 127),
    "CleanSectionFinalHeight": PI_V("Integer", "Range", 17),
    "CleanSectionFinalHeightCount": PI_V("Integer", "Range", 18),
    "CleanSerialRegister": PI_V("Boolean", "Collection", 19),
    "CleanCycleCount": PI_V("Integer", "Range", 20),
    "CleanCycleHeight": PI_V("Integer", "Range", 21),
    "CleanBeforeExposure": PI_V("Boolean", "Collection", 78),
    "CleanUntilTrigger": PI_V("Boolean", "Collection", 22),
}

PicamConstraintScope = c_int
PicamConstraintSeverity = c_int

# NOTE: PICam SDK uses pibln = piint = c_int (4 bytes) for booleans.
# c_bool is only 1 byte.  Struct layouts happen to match due to alignment
# padding on 64-bit, but we use c_int to be correct.


class PicamRangeConstraint(Structure):
    _fields_ = [
        ("scope", PicamConstraintScope), ("severity", PicamConstraintSeverity),
        ("empty_set", c_int), ("minimum", c_double), ("maximum", c_double),
        ("increment", c_double), ("excluded_values_array", POINTER(c_double)),
        ("excluded_values_count", c_int), ("outlying_values_array", POINTER(c_double)),
        ("outlying_values_count", c_int)
    ]


class PicamCollectionConstraint(Structure):
    _fields_ = [
        ("scope", PicamConstraintScope), ("severity", PicamConstraintSeverity),
        ("values_array", POINTER(c_double)), ("values_count", c_int)
    ]


class PicamCameraID(Structure):
    _fields_ = [("model", c_int), ("computer_interface", c_int), ("sensor_name", c_char * 64), ("serial_number", c_char * 64)]


class PicamRoi(Structure):
    _fields_ = [("x", c_int), ("width", c_int), ("x_binning", c_int), ("y", c_int), ("height", c_int), ("y_binning", c_int)]


class PicamRois(Structure):
    _fields_ = [("roi_array", POINTER(PicamRoi)), ("roi_count", c_int)]


class PicamRoisConstraint(Structure):
    _fields_ = [
        ("scope", PicamConstraintScope), ("severity", PicamConstraintSeverity),
        ("empty_set", c_int), ("rules", c_int), ("maximum_roi_count", c_int),
        ("x_constraint", PicamRangeConstraint), ("width_constraint", PicamRangeConstraint),
        ("x_binning_limits_array", POINTER(c_int)), ("x_binning_limits_count", c_int),
        ("y_constraint", PicamRangeConstraint), ("height_constraint", PicamRangeConstraint),
        ("y_binning_limits_array", POINTER(c_int)), ("y_binning_limits_count", c_int)
    ]


class PicamAvailableData(Structure):
    _pack_ = True
    _fields_ = [("initial_readout", c_void_p), ("readout_count", c_int64)]


class PicamAcquisitionStatus(Structure):
    _fields_ = [("running", c_int), ("errors", c_int), ("readout_rate", c_double)]


PicamTimeStampsMask = {"None": 0x0, "ExposureStarted": 0x1, "ExposureEnded": 0x2, "ReadoutStarted": 0x4, "ReadoutEnded": 0x8}

PICAM_ERROR_CODES = {
    0: "None", 1: "LibraryNotInitialized", 2: "InvalidParameterValue",
    3: "UnexpectedNullPointer", 4: "UnexpectedError", 5: "LibraryAlreadyInitialized",
    6: "InvalidDemoModel", 7: "CameraAlreadyOpened", 8: "InvalidCameraID",
    9: "InvalidHandle", 10: "ParameterValueIsReadOnly", 11: "ParameterHasInvalidValueType",
    12: "ParameterDoesNotExist", 13: "ParameterHasInvalidConstraintType",
    14: "ParameterValueIsIrrelevant", 15: "DeviceCommunicationFailed",
    16: "InvalidEnumeratedType", 17: "EnumerationValueNotDefined",
    18: "NotDiscoveringCameras", 19: "AlreadyDiscoveringCameras",
    20: "AcquisitionInProgress", 21: "InvalidDemoSerialNumber",
    22: "DemoAlreadyConnected", 23: "DeviceDisconnected", 24: "DeviceOpenElsewhere",
    25: "ParameterIsNotOnlineable", 26: "ParameterIsNotReadable",
    27: "AcquisitionNotInProgress", 28: "InvalidParameterValues",
    29: "ParametersNotCommitted", 30: "InvalidAcquisitionBuffer",
    31: "InsufficientMemory", 32: "TimeOutOccurred",
    33: "AcquisitionUpdatedHandlerRegistered", 34: "NoCamerasAvailable",
}


def picam_error_string(error_code: int) -> str:
    return f"PicamError_{PICAM_ERROR_CODES.get(error_code, f'Unknown({error_code})')}"


class PicamError(Exception):
    def __init__(self, error_code: int, operation: str = ""):
        self.error_code = error_code
        self.error_string = picam_error_string(error_code)
        self.operation = operation
        message = f"PICam error {error_code}: {self.error_string}"
        if operation:
            message = f"{operation}: {message}"
        super().__init__(message)


def picam_call(func, *args, operation: str = ""):
    error = func(*args)
    if error != 0:
        raise PicamError(error, operation or func.__name__)
    return error


# PicamHandle is void* ; PicamParameter and enums are int
_PH = c_void_p   # PicamHandle
_PP = c_int       # PicamParameter (encoded enum)
_PE = c_int       # PicamError return


def _configure_argtypes(picam: Union[CDLL, "WinDLL"]) -> None:
    """Declare argtypes and restype for every PICam function we call.

    Without these, ctypes on 64-bit Windows silently mangles pointer
    arguments, leading to intermittent segfaults inside the native DLL.
    """

    # --- Library lifecycle ---
    picam.Picam_InitializeLibrary.argtypes = []
    picam.Picam_InitializeLibrary.restype = _PE

    picam.Picam_UninitializeLibrary.argtypes = []
    picam.Picam_UninitializeLibrary.restype = _PE

    picam.Picam_GetVersion.argtypes = [POINTER(c_int), POINTER(c_int), POINTER(c_int), POINTER(c_int)]
    picam.Picam_GetVersion.restype = _PE

    # --- Camera discovery & connection ---
    picam.Picam_GetAvailableCameraIDs.argtypes = [POINTER(POINTER(PicamCameraID)), POINTER(c_int)]
    picam.Picam_GetAvailableCameraIDs.restype = _PE

    picam.Picam_DestroyCameraIDs.argtypes = [POINTER(PicamCameraID)]
    picam.Picam_DestroyCameraIDs.restype = _PE

    picam.Picam_ConnectDemoCamera.argtypes = [c_int, c_char_p, POINTER(PicamCameraID)]
    picam.Picam_ConnectDemoCamera.restype = _PE

    picam.Picam_OpenFirstCamera.argtypes = [POINTER(_PH)]
    picam.Picam_OpenFirstCamera.restype = _PE

    picam.Picam_OpenCamera.argtypes = [POINTER(PicamCameraID), POINTER(_PH)]
    picam.Picam_OpenCamera.restype = _PE

    picam.Picam_CloseCamera.argtypes = [_PH]
    picam.Picam_CloseCamera.restype = _PE

    # --- Enumeration strings ---
    picam.Picam_GetEnumerationString.argtypes = [c_int, c_int, POINTER(c_char_p)]
    picam.Picam_GetEnumerationString.restype = _PE

    picam.Picam_DestroyString.argtypes = [c_char_p]
    picam.Picam_DestroyString.restype = _PE

    # --- Parameter getters ---
    picam.Picam_GetParameterIntegerValue.argtypes = [_PH, _PP, POINTER(c_int)]
    picam.Picam_GetParameterIntegerValue.restype = _PE

    picam.Picam_GetParameterFloatingPointValue.argtypes = [_PH, _PP, POINTER(c_double)]
    picam.Picam_GetParameterFloatingPointValue.restype = _PE

    picam.Picam_ReadParameterFloatingPointValue.argtypes = [_PH, _PP, POINTER(c_double)]
    picam.Picam_ReadParameterFloatingPointValue.restype = _PE

    picam.Picam_GetParameterLargeIntegerValue.argtypes = [_PH, _PP, POINTER(c_int64)]
    picam.Picam_GetParameterLargeIntegerValue.restype = _PE

    picam.Picam_GetParameterRoisValue.argtypes = [_PH, _PP, POINTER(POINTER(PicamRois))]
    picam.Picam_GetParameterRoisValue.restype = _PE

    # --- Parameter setters ---
    picam.Picam_SetParameterIntegerValue.argtypes = [_PH, _PP, c_int]
    picam.Picam_SetParameterIntegerValue.restype = _PE

    picam.Picam_SetParameterFloatingPointValue.argtypes = [_PH, _PP, c_double]
    picam.Picam_SetParameterFloatingPointValue.restype = _PE

    picam.Picam_SetParameterLargeIntegerValue.argtypes = [_PH, _PP, c_int64]
    picam.Picam_SetParameterLargeIntegerValue.restype = _PE

    picam.Picam_SetParameterRoisValue.argtypes = [_PH, _PP, POINTER(PicamRois)]
    picam.Picam_SetParameterRoisValue.restype = _PE

    # --- Constraint getters ---
    picam.Picam_GetParameterRangeConstraint.argtypes = [_PH, _PP, c_int, POINTER(POINTER(PicamRangeConstraint))]
    picam.Picam_GetParameterRangeConstraint.restype = _PE

    picam.Picam_GetParameterCollectionConstraint.argtypes = [_PH, _PP, c_int, POINTER(POINTER(PicamCollectionConstraint))]
    picam.Picam_GetParameterCollectionConstraint.restype = _PE

    picam.Picam_GetParameterRoisConstraint.argtypes = [_PH, _PP, c_int, POINTER(POINTER(PicamRoisConstraint))]
    picam.Picam_GetParameterRoisConstraint.restype = _PE

    # --- Constraint destructors ---
    picam.Picam_DestroyRangeConstraints.argtypes = [POINTER(PicamRangeConstraint)]
    picam.Picam_DestroyRangeConstraints.restype = _PE

    picam.Picam_DestroyCollectionConstraints.argtypes = [POINTER(PicamCollectionConstraint)]
    picam.Picam_DestroyCollectionConstraints.restype = _PE

    picam.Picam_DestroyRoisConstraints.argtypes = [POINTER(PicamRoisConstraint)]
    picam.Picam_DestroyRoisConstraints.restype = _PE

    # --- Commit / parameters ---
    picam.Picam_CommitParameters.argtypes = [_PH, POINTER(POINTER(c_int)), POINTER(c_int)]
    picam.Picam_CommitParameters.restype = _PE

    picam.Picam_DestroyParameters.argtypes = [POINTER(c_int)]
    picam.Picam_DestroyParameters.restype = _PE

    # --- Acquisition ---
    picam.Picam_IsAcquisitionRunning.argtypes = [_PH, POINTER(c_int)]
    picam.Picam_IsAcquisitionRunning.restype = _PE

    picam.Picam_StartAcquisition.argtypes = [_PH]
    picam.Picam_StartAcquisition.restype = _PE

    picam.Picam_StopAcquisition.argtypes = [_PH]
    picam.Picam_StopAcquisition.restype = _PE

    picam.Picam_WaitForAcquisitionUpdate.argtypes = [_PH, c_int, POINTER(PicamAvailableData), POINTER(PicamAcquisitionStatus)]
    picam.Picam_WaitForAcquisitionUpdate.restype = _PE

    logger.debug("PICam argtypes configured for all functions")


def load_picam_library(library: str, dll_directories: List[str] = None) -> Optional[CDLL]:
    """Load the PICam library, setting up DLL search directories on Windows."""
    try:
        if IS_WINDOWS and dll_directories:
            for d in dll_directories:
                if os.path.isdir(d):
                    os.add_dll_directory(d)
                    logger.debug(f"added DLL directory: {d}")

        if IS_WINDOWS:
            from ctypes import WinDLL
            picam = WinDLL(library)
        else:
            picam = CDLL(library)

        logger.debug(f"loaded PICam library from {library}")
        _configure_argtypes(picam)
        return picam
    except OSError as e:
        logger.error(f"Failed to load PICam library from {library}: {e}")
        return None


PicamModel = {
    "Pixis100Series": 0, "Pixis256Series": 26, "Pixis400Series": 40,
    "Pixis512Series": 53, "Pixis1024Series": 76, "Pixis2048Series": 77,
    "Pixis2KBSeries": 104, "Pixis100BSeriesUV": 116,
    "ProEMSeries": 112, "ProEMPlusSeries": 143, "ProEMHSSeries": 163,
    "PyLoNSeries": 109, "PyLoN400Series": 180, "PyLoNIRSeries": 259,
    "Cosmos8k": 2801, "Cosmos16k": 2802,
    "Sophia2048Series": 169, "Sophia4096Series": 252,
}