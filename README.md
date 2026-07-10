# Arducam IMX708 Focus Control for NVIDIA Jetson

This repository provides focus control utilities for the **Sony IMX708 (Raspberry Pi Camera Module 3)** autofocus module on the NVIDIA Jetson platform. 

It includes a baseline manual focus tool and an advanced, high-performance, interactive dual-threaded Auto-Focus (AF) tuning utility.


## How to Find Your Camera's I2C Bus

On NVIDIA Jetson carrier boards, the CSI camera's VCM motor is typically bound to either **I2C Bus 9** or **I2C Bus 10**. 

To determine exactly which bus your IMX708 is registered on, execute the following command in your terminal:

```bash
sudo dmesg | grep imx708
```

**Example Log Output:**
```
[1.824901] imx708 9-001a: Detected IMX708 sensor
```
In the example above, 9-001a indicates the camera is registered on I2C Bus 9. You would run the focus scripts using **-i 9**.

## Usage Instructions

This repository contains two focus control applications depending on your requirements:

### 1. Basic Focus Tool (FocuserExample.py)
A lightweight baseline demonstration script for simple step-by-step manual focus adjustment via terminal parameters.
To run the basic manual focus utility (replace 9 with your detected I2C bus):
```bash
python3 FocuserExample.py -i 9
```
### 2. Advanced Focus Tuner (FocusTuner.py) - Recommended
A high-performance, interactive calibration tool featuring a real-time HUD (Heads-Up Display) overlay and Autofocus function.
To run the advanced tuner (replace 9 with your detected I2C bus):
```bash
python3 FocusTuner.py -i 9
```
**Interactive HUD Controls:**

Once the video window opens, click on the window to focus it, then use the following keys:

[ UP / DOWN Arrows ] : Manually step the lens focus forward or backward.

[ F ] : Trigger the fully-automatic 2-Pass Autofocus sequence.

[ R ] : Reset the lens to the factory default datum.

[ S ] : Capture and save a clean, high-resolution visual asset (.jpg) to disk without HUD overlays.

[ ESC ] : Toggle the HUD overlay dashboard visibility on/off.

[ Q ] : Initiate safe program termination and release daemon channels.
