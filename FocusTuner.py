import cv2
import time
import argparse
import threading
from datetime import datetime

from Focuser import Focuser          
from JetsonCamera import Camera      

def calculate_sharpness(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.Laplacian(gray, cv2.CV_64F).var()

def draw_target_bracket(img, x1, y1, x2, y2, color, thickness=2, length=20):
    cv2.line(img, (x1, y1), (x1+length, y1), color, thickness)
    cv2.line(img, (x1, y1), (x1, y1+length), color, thickness)
    cv2.line(img, (x2, y1), (x2-length, y1), color, thickness)
    cv2.line(img, (x2, y1), (x2, y1+length), color, thickness)
    cv2.line(img, (x1, y2), (x1+length, y2), color, thickness)
    cv2.line(img, (x1, y2), (x1, y2-length), color, thickness)
    cv2.line(img, (x2, y2), (x2-length, y2), color, thickness)
    cv2.line(img, (x2, y2), (x2, y2-length), color, thickness)

class BackgroundVCMWorker(threading.Thread):
    def __init__(self, focuser, initial_target=439):
        threading.Thread.__init__(self)
        self.focuser = focuser
        self.target = initial_target
        self.current = initial_target
        self.running = True

    def run(self):
        while self.running:
            if self.target != self.current:
                self.focuser.set(Focuser.OPT_FOCUS, self.target)
                self.current = self.target
            time.sleep(0.015) 

    def update_hardware(self, val):
        self.target = val

    def block_until_synced(self):
        while self.current != self.target and self.running:
            time.sleep(0.005)

    def end_task(self):
        self.running = False


def run_autofocus(camera, motor_worker, window_name):
    print("\n=======================================================")
    print("[HYPER-AF] ENGAGING 1.4S HIGH-SPEED PRECISION PROTOCOL...")
    print("=======================================================")
    
    alpha = globals().get('ALPHA_CONTRAST', 1.25)
    beta = globals().get('BETA_BLACK_LVL', -25)

    temp_frame = camera.getFrame()
    if temp_frame is None: return motor_worker.target
    h, w = temp_frame.shape[:2]
    
    # ROI Setup (Central 30% area)
    x1, y1 = int(w * 0.35), int(h * 0.35)
    x2, y2 = int(w * 0.65), int(h * 0.65)

    # ----------------------------------------------------
    # INNER FUNCTION: Highly Optimized Frame Acquisition
    # ----------------------------------------------------
    def get_sharpness_score(target_val, is_fine_phase=False):
        motor_worker.update_hardware(target_val)
        motor_worker.block_until_synced()
        
        # ASYMMETRICAL TIMING:
        # Fine phase needs perfect settling. Coarse phase can run in ultra-fast drift mode.
        if is_fine_phase:
            time.sleep(0.045)     # Settle VCM
            flush_frames = 4      # Flush buffer
        else:
            time.sleep(0.015)     # Micro-settle for rapid transit
            flush_frames = 2      # Minimal flush for peak estimation
            
        for _ in range(flush_frames):
            camera.getFrame()
            
        frame = camera.getFrame()
        if frame is None: return 0.0
            
        frame = cv2.convertScaleAbs(frame, alpha=alpha, beta=beta)
        roi = frame[y1:y2, x1:x2]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 1.5)
        return cv2.Laplacian(blurred, cv2.CV_64F).var()

    # ----------------------------------------------------
    # PHASE 1: HYPER-FAST COARSE SWEEP
    # Range: 350 to 950 (Truncates dead infinity zones < 350)
    # ----------------------------------------------------
    print("[AF_SPEED] Launching Rapid Macro Sweep (Step: 100)...")
    coarse_best_val = 450
    coarse_max_sharpness = 0.0
    t0 = time.time()
    
    # 7 ultra-fast steps across the useful diopter spectrum of IMX708
    for val in range(350, 960, 100):
        sharpness = get_sharpness_score(val, is_fine_phase=False)
        if sharpness > coarse_max_sharpness:
            coarse_max_sharpness = sharpness
            coarse_best_val = val
            
        # Optional: Render fast UI
        frame = camera.getFrame()
        if frame is not None:
            frame = cv2.convertScaleAbs(frame, alpha=alpha, beta=beta)
            draw_target_bracket(frame, x1, y1, x2, y2, (0, 150, 255), 3, 30)
            cv2.putText(frame, f"FAST COARSE SCAN: {val}", 
                        (40, h - 80), cv2.FONT_HERSHEY_DUPLEX, 0.9, (0, 150, 255), 2)
            cv2.imshow(window_name, frame)
            cv2.waitKey(1)

    # ----------------------------------------------------
    # PHASE 2: HIGH-PRECISION FINE NEURAL SWEEP
    # Narrow search bounds (±75) around coarse peak with step 15
    # ----------------------------------------------------
    fine_start = max(350, coarse_best_val - 75)
    fine_end   = min(950, coarse_best_val + 75)
    fine_step  = 15 # Wide stride optimized for parabolic curve mapping
    
    print(f"[AF_SPEED] Launching Precision Micro Sweep [{fine_start} - {fine_end}]...")
    
    # Rapid backoff to align hysteresis gears
    motor_worker.update_hardware(max(0, fine_start - 30))
    motor_worker.block_until_synced()
    time.sleep(0.10)
    
    sweep_results = []
    
    for val in range(fine_start, fine_end + 1, fine_step):
        sharpness = get_sharpness_score(val, is_fine_phase=True)
        sweep_results.append((val, sharpness))
        
        frame = camera.getFrame()
        if frame is not None:
            frame = cv2.convertScaleAbs(frame, alpha=alpha, beta=beta)
            draw_target_bracket(frame, x1, y1, x2, y2, (20, 255, 100), 2, 40)
            cv2.putText(frame, f"FINE RESOLUTION SCAN: {val}", 
                        (40, h - 80), cv2.FONT_HERSHEY_DUPLEX, 0.9, (20, 255, 100), 2)
            cv2.imshow(window_name, frame)
            cv2.waitKey(1)

    # ----------------------------------------------------
    # MATHEMATICAL PARABOLIC PEAK FITTING (Sub-step Extraction)
    # ----------------------------------------------------
    max_idx = max(range(len(sweep_results)), key=lambda i: sweep_results[i][1])
    
    if 0 < max_idx < len(sweep_results) - 1:
        x0, y0 = sweep_results[max_idx - 1]
        x1, y1 = sweep_results[max_idx]
        x2, y2 = sweep_results[max_idx + 1]
        
        denom = 2.0 * (y0 - 2.0 * y1 + y2)
        if abs(denom) > 1e-5:
            # Interpolating the peak at diopter fractional limits
            raw_peak = x1 + (fine_step * (y0 - y2)) / denom
            final_best_val = int(round(raw_peak))
        else:
            final_best_val = x1
    else:
        final_best_val = sweep_results[max_idx][0]
        
    final_best_val = max(350, min(950, final_best_val))
    print(f"[AF_MATH] Fitted Curve Apex Resolved at: [ {final_best_val} ]")

    # ----------------------------------------------------
    # ANTI-HYSTERESIS SINGLE-DIRECTION APPROACH
    # ----------------------------------------------------
    backoff_target = max(0, final_best_val - 40)
    motor_worker.update_hardware(backoff_target)
    motor_worker.block_until_synced()
    time.sleep(0.10)  
    
    motor_worker.update_hardware(final_best_val)
    motor_worker.block_until_synced()
    time.sleep(0.06)
    
    # Clean pipeline
    for _ in range(4):
        camera.getFrame()
        
    t_elap = time.time() - t0
    print(f"[SUCCESS] OPTICAL AXIS LOCK SECURED IN {t_elap:.2f} SECONDS.\n")
    return final_best_val

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--i2c-bus', type=int, required=True, 
                        help="Gives standard control bus to link component (ex: -i 9).")
    args = parser.parse_args()

    # Step 1: Open motor
    try:
        focuser = Focuser(args.i2c_bus)
    except Exception as e:
        print("[FAIL] Driver I2C Error.")
        return

    # Step 2: WAIT on starting threads until Jetson finishes loading full NVargus flow.
    print("[INIT] Summoning Streamer Backend Process..")
    try:
        cam = Camera() 
    except RuntimeError:
        print("[FATAL] Camera sensor could not resolve/is in Use!")
        return
        
    time.sleep(1.5)  # MUST WAIT HERE BEFORE MULTI THREAD HARDWARE INVOKED TO PREVENT NVDAEMON FREEZING OS 

    # Base start configs! Safe to start threading agent engine since we already sleep allowed Jetson base ready
    focus_value = 439 
    focus_step  = 16   
    focuser.set(Focuser.OPT_FOCUS, focus_value) 
    
    # Initialize Engine Now that memory handles are decoupled
    worker = BackgroundVCMWorker(focuser, focus_value)
    worker.start()

    WINDOW = "MATRIX V.8 NEURAL SYSTEM APP GUI //"
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(WINDOW, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    print("\n>>>> ONLINE AND STABLE >> USE DISPLAY VISUALS! \n")

    is_ui_vis = True     
    pop_text, pop_timer = "", 0
    hud_lit_id, hud_lit_timer = -1, 0 

    while True:
        frame = cam.getFrame()
        if frame is None:
            break
            
        clean_snapshot_raw = frame.copy() # Safe Raw output buffer 
        h, w = frame.shape[:2]

        # -------------- USER EXPERIENCE FRONT END -----------
        if is_ui_vis:
            overlay = frame.copy()
            cv2.rectangle(overlay, (20, 20), (530, 270), (0, 2, 4), -1)
            frame = cv2.addWeighted(overlay, 0.70, frame, 0.30, 0)
            
            # Sci-Fi border outline structure!
            cv2.rectangle(frame, (20, 20), (530, 270), (0, 190, 5), 1)

            menus = [
                f" LENS_COORD: << {focus_value} >> | STAT: ACTIVE ",
                " --------------------------------",
                "   UP / DWN  >   VCM TWEAK PROTOCOL     [0x0]",
                "     F KEY    >   TRIGGER AUTO-ALIGN       [AF+]",
                "     R KEY    >   PURGE & RESET VCM       [RES]",
                "     S KEY    >   EXPORT CLEAN IMAGE       [IMG]",
                "    ESC KEY   >   HUD DISPLAY OVERRIDE    [INV]",
                "     Q KEY    >   TERMINATE DAEMON        [BRK]"
]

            font_off, spacing = 58, 26 
            
            for index, info_txt in enumerate(menus):
                fz_sz, txt_col, fw_sz = 0.58, (170, 215, 230), 1
                
                # Active Lens Monitor header 
                if index == 0: txt_col, fw_sz = ((20, 250, 45), 2)
                elif index == 1: txt_col = (110, 110, 100) # Dim dashes
                
                base_position = (35, font_off + index * spacing)

                # DYNAMIC LED FEEDBACK ILLUMINATOR EFFECTS IF PRESSES ENGAGED !
                if index == hud_lit_id and hud_lit_timer > 0:
                    bx, _ = cv2.getTextSize(info_txt, cv2.FONT_HERSHEY_SIMPLEX, fz_sz, fw_sz)
                    
                    p1 = (base_position[0] - 6, base_position[1] - bx[1] - 4)
                    p2 = (base_position[0] + bx[0] + 6, base_position[1] + _ + 3)
                    
                    cv2.rectangle(frame, p1, p2, (255, 140, 0), -1) # Strike an inverted highlight Orange neon shape 
                    txt_col = (5, 5, 8) 
                    fw_sz = 2 
                    hud_lit_timer -= 1 

                cv2.putText(frame, info_txt, base_position, 
                            cv2.FONT_HERSHEY_SIMPLEX, fz_sz, txt_col, fw_sz)

        # BIG Event prompt popup (like Save pictures action texts updates rendering)!
        if pop_timer > 0:
            box_r, _ = cv2.getTextSize(pop_text, cv2.FONT_HERSHEY_TRIPLEX, 0.9, 2)
            cv2.putText(frame, pop_text, ((w - box_r[0]) // 2, h - 80), 
                        cv2.FONT_HERSHEY_TRIPLEX, 0.9, (120, 245, 120), 2)
            pop_timer -= 1
        
        cv2.imshow(WINDOW, frame)

        # --------------- EVENT PIPELINE BUS ----------------
        k = cv2.waitKey(20) & 0xFF  
        if k == 255: continue 
            
        # -- DISPLAY REVEAL(Esc) -> HUD ENABLE / DE_RENDER VISUAL OFF! 
        if k == 27: is_ui_vis = not is_ui_vis

        # -- Q (or q) -> TRIGGER SHUT DOWN ANIMATE/QUIT SECURE! 
        elif k == ord('q') or k == ord('Q'): 
            hud_lit_id, hud_lit_timer = (7, 10) 
            d_lay = frame.copy()
            cv2.rectangle(d_lay, (0,0), (w,h), (12, 0, 75), -1)
            frame = cv2.addWeighted(d_lay, 0.85, frame, 0.15, 0)
            cv2.putText(frame, "> SYS KILLED IN BACKGROUND, DO NOT ESC ABRUPT...", (200, h//2), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (60, 200, 255), 3)
            cv2.imshow(WINDOW, frame); cv2.waitKey(350) 
            break  
            
        # -- THE SMOOTH RESPONDING MOTOR HARDWARE PIPING (Continuous Holds Allow Tracking Value Seamless Slider Effect).  
        elif k == 0 or k == 82: # Strict PURE system keyboard 'Up Arrow'! 
            focus_value = min(focus_value + focus_step, 1023)
            worker.update_hardware(focus_value) # Instant non locking sync value 
            hud_lit_id, hud_lit_timer = (2, 8)

        elif k == 1 or k == 84: # Strict PURE 'Down Arrow'! 
            focus_value = max(focus_value - focus_step, 0)
            worker.update_hardware(focus_value)
            hud_lit_id, hud_lit_timer = (2, 8) 
            
        # -- CAPTURE A PHOTO ON EXPLICIT HIT NO CLICKS  
        # Using ONLY Key Letter [s, S] -> Save Pure frame (NO FOCUS MIX_UPS WITH HARD ARROWS AGAIN!!)
        elif k == ord('s') or k == ord('S'): 
            fn = datetime.now().strftime("HQ_OPT_%Y_%H%M%S.jpg")
            if cv2.imwrite(fn, clean_snapshot_raw): # Passes through our stripped no layer picture reference !
                pop_text = f" -> ASSET CONFIRMED AND FLUSHED RAW IMAGE DISK >> {fn}"
                pop_timer = 50 
                hud_lit_id, hud_lit_timer = (5, 10) 
            
        # -- DATUM ENGINE ZERO  
        elif k == ord('r') or k == ord('R'): 
            focus_value = 439
            worker.update_hardware(focus_value)
            pop_text, pop_timer, hud_lit_id, hud_lit_timer = ("[CALIBRATED AXIS CLEARED AND RESET STATE].", 35, 4, 10)
            
        # -- NEURAL DEEP AF MACRO SCAN ! 
        elif k == ord('f') or k == ord('F'): 
            hud_lit_id, hud_lit_timer = (3, 8)
            # Re update our variables off thread completion. 
            focus_value = run_autofocus(cam, worker, WINDOW) 
            pop_text, pop_timer = (f"++ LENS TRAINED ALIGNMENT FULL ACQUIRED SCORE >> [ {focus_value} ]", 75)


    # FINAL END SAFE
    print("[EXIT PROTOCOL ENGINES OFF] >>> Safe Motor Destructing")
    worker.end_task()
    worker.join() 
    
    print("Clearing Argus Stream buffers properly... Allow Moment to Close.")
    cam.close()    
    cv2.destroyAllWindows()
    print("TERMINAL FINISHED END. EXIT SAFE.\n")

if __name__ == '__main__':
    main()

