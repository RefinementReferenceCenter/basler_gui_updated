"""
    @author Niek Andresen
    @date May 2020
    
    At the core of this script lies the multicamera code from the pypylon samples.
    I adjusted it for our purposes.
    
    It is supposed to be used in some GUI. After start_recording has been called, all
    connected Basler cameras will record.
    
    There is a preview window opening, when recording. The grabbing of frames and the
    writing of the video are done in a separate thread from the previewing.
    
    Camera settings are hard coded below (in "set_cam_settings").
"""

import os
from pypylon import pylon
from pathlib import Path
import time
from datetime import datetime, timedelta
import threading
import cv2
import platform
from bedsy.bedsy import Bedsy
import queue
from collections import deque

if platform.system() == 'Windows':
    from reset_USB import reset_baslers_windows as reset_baslers
else:
    from reset_USB import reset_baslers_linux as reset_baslers

from logger import Logger
import b_record_to_vid as r2v

class BaslerMouseRecorder():

    def replace_backslash_in_dir(d):
        d = d.replace('\\\\', '\\')
        d = d.replace('\\', '/')
        return d

    def __init__(self, vid_dir, size=(1936,1216), fps=41, ffmpeg='ffmpeg', use_bedsy=False, bedsy_fps=None):
        # switch this (True/False) to use BeDSy, an external bedsy device for triggering frame captures
        self.use_bedsy = use_bedsy

        self.bedsy_fps = float(bedsy_fps)
        self.size = size
        self.fps = fps
        self.total_t = 0
        self.fpre = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime()) # file prefix
        self.vid_dir = Path(BaslerMouseRecorder.replace_backslash_in_dir(vid_dir))
        if self.use_bedsy:
            self.vid_dir = self.vid_dir / self.fpre
        self.set_logfile()
        self.ffmpeg_command = ffmpeg
        self.manager_running = False
        self.writers_running = False
        #self.writer_thread = None
        self.c_threads = dict()
        self.use_dummy_camera = False
        self.writers_ready = {}
        reset_baslers()

    def set_logfile(self):
        self.fpre = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime()) # file prefix
        self.logger = Logger(self.vid_dir)

    def set_cam_settings(self, cam, i):
        cam.Attach(self.tlFactory.CreateDevice(self.devices[i]))
        cam.Open()
        if cam.DeviceInfo.GetDeviceClass() == "BaslerCamEmu":
            cam.RegisterConfiguration(pylon.SoftwareTriggerConfiguration(), pylon.RegistrationMode_ReplaceAll,
                                      pylon.Cleanup_Delete)
        cam.OffsetX = 0
        cam.OffsetY = 0
        cam.Width = self.size[0]
        cam.Height = self.size[1]
        cam.PixelFormat = "Mono8"
        cam.ExposureAuto = "Off"
        cam.ExposureTime = 20000 # microseconds
        #cam.DeviceLinkThroughputLimitMode = "Off"
        if self.use_bedsy:
            cam.TriggerMode.SetValue('On')
            cam.TriggerDelay.SetValue(0)
            cam.TriggerSelector.SetValue('FrameStart')
            if cam.DeviceInfo.GetDeviceClass() != "BaslerCamEmu":
                cam.TriggerSource.SetValue('Line3')
            cam.TriggerActivation.SetValue('RisingEdge')
            cam.AcquisitionMode.SetValue('Continuous')
            cam.AcquisitionFrameRateEnable = False
            cam.AcquisitionFrameRate = 30
            cam.AcquisitionStatusSelector.SetValue('FrameTriggerWait')
        else:
            cam.AcquisitionFrameRateEnable = True
            cam.AcquisitionFrameRate = 30 # the frame rate that he is trying to do
            # he estimates the actual frame rate in cam.ResultingFrameRate. Tests have shown that this is also not always accurate though - especially when using 12 bit pixels (Mono12p).
        if cam.DeviceInfo.GetDeviceClass() == "BaslerCamEmu":
            self.use_dummy_camera = True
            cam.TestImageSelector.SetValue("Testimage2")

    def get_cam_settings(self, cam):
        result = []
        result.append("Size Width: {}\n".format(cam.Width.Value))
        result.append("Size Height: {}\n".format(cam.Height.Value))
        result.append("Offset X: {}\n".format(cam.OffsetX.Value))
        result.append("Offset Y: {}\n".format(cam.OffsetY.Value))
        result.append("Pixel Format: {}\n".format(cam.PixelFormat.Value))
        result.append("Exposure Auto: {}\n".format(cam.ExposureAuto.Value))
        result.append("Exposure Time: {}\n".format(cam.ExposureTime.Value))
        result.append("Throughput Limit Mode: {}\n".format(cam.DeviceLinkThroughputLimitMode.Value))
        result.append("Acquisition Frame Rate Enable: {}\n".format(cam.AcquisitionFrameRateEnable.Value))
        result.append("Acquisition Frame Rate: {}\n".format(cam.AcquisitionFrameRate.Value))
        if not self.use_bedsy:
            result.append("->Resulting Frame Rate: {}\n".format(cam.ResultingFrameRate.Value))
        return "".join(result)

    # def start_writing_frames(self):
    #     self.writer_ready = False
    #     if self.use_bedsy:
    #         self.cameras.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
    #     else:
    #         self.cameras.StartGrabbing()
    #     #time.sleep(3)
    #     first_frame = True
    #     while self.writers_running:
    #         if self.use_dummy_camera:
    #             for c in self.cameras:
    #                 if c.DeviceInfo.GetDeviceClass() == "BaslerCamEmu":
    #                     c.ExecuteSoftwareTrigger()
    #         if self.use_bedsy:
    #             if first_frame:
    #                 while not all([c.AcquisitionStatus.GetValue() for c in self.cameras]):
    #                     time.sleep(0.1)
    #                 first_frame = False
    #                 self.writer_ready = True
    #                 self.start_t = self.logger.logWithTime("Started recording with {} Basler camera{}.".format(self.num_cams, 's' if self.num_cams>1 else ''), stdout=True)
    #             grabResult = self.cameras.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
    #         else:
    #             grabResult = self.cameras.RetrieveResult(500, pylon.TimeoutHandling_ThrowException)
    #             self.start_t = self.logger.logWithTime("Started recording with {} Basler camera{}.".format(self.num_cams, 's' if self.num_cams>1 else ''), stdout=True)
    #         serial = self.cameras[grabResult.GetCameraContext()].DeviceInfo.GetSerialNumber()
    #         # Write image to video
    #         frame = grabResult.GetArray()
    #         self.frames[serial] = frame
    #         self.writers[serial].write_frame(frame)
    #         grabResult.Release()
    #         self.frame_counter_dict[serial] += 1
    #     self.end_t = time.time()
    #     self.cameras.StopGrabbing()

    def cam_start_writing_frames(self, c, serial):
        if self.use_bedsy:
            c.StartGrabbing(pylon.GrabStrategy_LatestImageOnly)
            #From Aaron: Ich vermute das mit den 6 cameras manchmal es daurets ein tick zu lagen die frame zu speichern/coderien. Mit die GrabStrategy_LatestImageOnly
            # wird dann immer nur die neueste frame geholt und dadurch wird frames verloren. Mann konnte GrabStrategy_OneByOne nutzen aber dann muss die loop geandert zu
            # holen alle verfugbare frames. Wir haben scnhell versucht mit mpeg4 statt h264 zu gucken ob es lag an die codec aber hab die gleiche ergebniss.
        else:
            c.StartGrabbing()
        #time.sleep(3)
        first_frame = True
        while self.writers_running:
            if self.use_dummy_camera:
                if c.DeviceInfo.GetDeviceClass() == "BaslerCamEmu":
                    c.ExecuteSoftwareTrigger()
            if self.use_bedsy:
                if first_frame:
                    while not c.AcquisitionStatus.GetValue():
                        time.sleep(0)
                    first_frame = False
                    self.writers_ready[serial] = True
                    #self.start_t = self.logger.logWithTime("Started recording with {} Basler camera{}.".format(self.num_cams, 's' if self.num_cams>1 else ''), stdout=True)
                grabResult = c.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)
            else:
                grabResult = c.RetrieveResult(500, pylon.TimeoutHandling_ThrowException)
                #self.start_t = self.logger.logWithTime("Started recording with {} Basler camera{}.".format(self.num_cams, 's' if self.num_cams>1 else ''), stdout=True)
            #serial = self.cameras[grabResult.GetCameraContext()].DeviceInfo.GetSerialNumber()
            # Write image to video
            frame = grabResult.GetArray()
            #self.frames[serial] = frame
            self.frames[serial].append(frame)
            self.writers[serial].write_frame(frame)
            grabResult.Release()
            self.frame_counter_dict[serial] += 1
            time.sleep(0)
        #self.end_t = time.time()
        c.StopGrabbing()

    # def start_writing_frames_in_thread(self):
    #     self.writer_thread = threading.Thread(target=self.start_writing_frames, args=())
    #     self.writer_thread.daemon = True
    #     self.writer_thread.start()
        
    def cam_start_writing_frames_in_thread(self):
        for c in self.cameras:
            serial = c.GetDeviceInfo().GetSerialNumber()
            self.c_threads[serial] = threading.Thread(target=self.cam_start_writing_frames, args=(c, serial))
            self.c_threads[serial].daemon = True
            self.writers_ready[serial] = False
            self.c_threads[serial].start()
            self.frames[serial] = deque(maxlen=1)
        self.start_t = self.logger.logWithTime("Started recording with {} Basler camera{}.".format(self.num_cams, 's' if self.num_cams>1 else ''), stdout=True)

    def start_recording(self):
        self.set_logfile()
        self.logger.startLogging()
        self.frame_counter_dict = dict()
        self.frames = dict()
        recmanager_thread = threading.currentThread()

        #self.manager_running = True
        maxCamerasToUse = 30

        # Get the transport layer factory.
        self.tlFactory = pylon.TlFactory.GetInstance()

        # Get all attached devices and exit application if no device is found.
        # Rescan for 5 seconds before giving up
        self.devices = None
        timeout = timedelta(seconds=5)
        start = datetime.now()
        while (not self.devices) and ((datetime.now()-start) < timeout):
            self.devices = self.tlFactory.EnumerateDevices()
        if len(self.devices) == 0:
            self.logger.log("Cannot start: No Basler camera found.", stdout=True)
            self.logger.closeLogger()
            return 1
        try:
            bedsy_initialised = False
            #print("running main loop")
            while getattr(recmanager_thread, "thread_running"):
                #print("running recurring loop")
                do_rollover = False
                self.writers_running = True # this flag controls the thread which grabs frames and writes them to a file
                # start up the bedsy
                if self.use_bedsy and not bedsy_initialised:
                    #print("DEBUG", "Initializing BeDSy...")
                    q = queue.Queue()
                    bedsy = Bedsy(q, ["VID:PID=16C0:0483", "SER=13567420"]) # teensy 4.0                
                    #bedsy = Bedsy(q, ["VID:PID=16C0:0483", "SER=14487510"]) # teensy 4.1
                # Create an array of instant cameras for the found devices and avoid exceeding a maximum number of devices.
                # Attach all Pylon Devices, make settings and create writers.
                self.cameras = pylon.InstantCameraArray(min(len(self.devices), maxCamerasToUse))
                # Make the setup for each cam, log that it was found, create a writer for it etc.
                self.writers = dict()
                self.serials = []
                self.num_cams = 0
                for n, cam in enumerate(self.cameras):
                    self.set_cam_settings(cam, n)
                    serial = cam.DeviceInfo.GetSerialNumber()
                    self.serials.append(serial)
                    self.logger.log("Found Basler cam {} ({}).".format(serial, cam.GetDeviceInfo().GetModelName()), stdout=True)
                    self.logger.log("Settings:", stdout=False)
                    self.logger.log(self.get_cam_settings(cam), stdout=False)
                    self.num_cams += 1
                #while getattr(recmanager_thread, "thread_running", True):
                    #for n, cam in enumerate(self.cameras):
                    #    self.set_cam_settings(cam, n)
                    #self.writers_running = True
                    if self.use_bedsy:
                        # Make sure the filename changes for rollover logs
                        fpre_upd = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())
                        vid_fname = str(self.vid_dir / (fpre_upd+'_'+serial+'_rec.avi'))
                        print(vid_fname)
                    else:
                        vid_fname = str(self.vid_dir / (self.fpre+'_'+serial+'_rec.avi'))
                    pixfmt = 'gray' if cam.PixelFormat.Value=='Mono8' else ('gray12le' if cam.PixelFormat.Value=='Mono12p' else 'error')
                    if self.use_bedsy:
                        self.writers[serial] = r2v.FFMPEG_VideoWriter(vid_fname, self.size, fps=self.bedsy_fps, pixfmt=pixfmt, ffmpeg_command=self.ffmpeg_command)
                    else:
                        self.writers[serial] = r2v.FFMPEG_VideoWriter(vid_fname, self.size, fps=cam.ResultingFrameRate.Value, pixfmt=pixfmt, ffmpeg_command=self.ffmpeg_command)
                    self.frame_counter_dict[serial] = 0
                # Start grabbing and writing to video file
                self.cam_start_writing_frames_in_thread()
                self.logger.logWithTime("Started recording.", stdout=True)
                if self.use_bedsy and not bedsy_initialised:
                    #print("DEBUG","Hello")
                    while not all(self.writers_ready):
                        time.sleep(0)
                    #print("DEBUG", "Starting BeDSy...")
                    bedsy.start_bedsy()
                    got_start_msg = False
                    msg = q.get(timeout=3)
                    #print("DEBUG", msg)
                    if "[START]" not in msg[1]:
                        #print("DEBUG", "[START] not in msg")
                        while q.qsize() > 0:
                            msg = q.get()
                            #print("DEBUG", msg)
                            if "[START]" in msg[1]:
                                break
                    if "[START]" not in msg[1]:
                        raise IOError("Problem with the BeDSy!")
                    else:
                        self.logger.logWithTime("BeDSy started.")
                        #print("DEBUG", "BeDSy started.")
                        bedsy_initialised = True
                        
                #time.sleep(1)
                # Display current frames
                for serial in self.serials:
                    cv2.namedWindow(f'Basler {serial}', cv2.WINDOW_NORMAL)
                    cv2.resizeWindow(f'Basler {serial}', 968, 608)
                while getattr(recmanager_thread, "thread_running") and not do_rollover:
                    for serial in self.serials:
                        if serial in self.frames:
                            try:
                                cv2.imshow(f'Basler {serial}', self.frames[serial].pop())
                            except IndexError:
                                pass
                    if self.use_bedsy:
                        try:
                            # try to get a message from the queue
                            # they will be tuples:
                            # first item: isoformat timestamp, second item: message
                            msg = q.get(block=False)
                        except queue.Empty:
                            cv2.waitKey(1)
                            time.sleep(0)
                        else:
                            if "[STOP_ROLLOVER]" in msg[1]:
                                self.writers_running = False
                                self.end_t = time.time()
                                self.logger.logWithTime("Recording rollover...", stdout=True)
                                cv2.destroyAllWindows()
                                #self.writer_thread.join()
                                for t in self.c_threads.values():
                                    t.join()
                                for writer in self.writers.values(): writer.close()
                                self.frames = dict()
                                frame_avg = sum([f for f in self.frame_counter_dict.values()]) / len(self.frame_counter_dict)
                                self.total_t += self.end_t-self.start_t
                                self.logger.log("Recorded {} frames in about {:.2f} seconds ({}) -> about {:.2f} fps.".format(self.frame_counter_dict, self.end_t-self.start_t, self.logger.durationToTimeStr(self.start_t,self.end_t), frame_avg/(self.end_t-self.start_t)), stdout=True)
                                #for serial in self.serials:
                                #    cv2.namedWindow(f'Basler {serial}', cv2.WINDOW_NORMAL)
                                #    cv2.resizeWindow(f'Basler {serial}', 968, 608)
                                do_rollover = True
                    else:
                        cv2.waitKey(750) # ms
                        time.sleep(0)

        finally: # Clean up and log the fps
            self.writers_running = False
            setattr(recmanager_thread, "thread_running", False)
            self.manager_running = False
            if self.use_bedsy:
                bedsy.stop_bedsy()
                msg = q.get(timeout=3)
                if "[STOP_PERMANENT]" not in msg[1]:
                    while q.qsize() > 0:
                        msg = q.get()
                        if "[STOP_PERMANENT]" in msg[1]:
                            self.logger.logWithTime("BeDSy stopped.")
                            bedsy_initialised = False
                            break
            self.logger.logWithTime("Stopped recording.", stdout=True)
            time.sleep(1)
            cv2.destroyAllWindows()
            #self.cameras.StopGrabbing()
            #if self.writer_thread is not None and self.writer_thread.is_alive(): self.writer_thread.join()
            #self.writer_thread = None
            for t in self.c_threads.values():
                if t.is_alive():
                    t.join()
            self.end_t = time.time()
            self.c_threads = dict()
            for writer in self.writers.values(): writer.close()
            frame_avg = sum([f for f in self.frame_counter_dict.values()]) / len(self.frame_counter_dict)
            #if not self.use_bedsy:
            self.total_t = self.end_t-self.start_t
            self.logger.log("\nRecorded {} frames in about {:.2f} seconds ({}) -> about {:.2f} fps.".format(self.frame_counter_dict, self.total_t, self.logger.durationToTimeStr(self.total_t), frame_avg/(self.total_t)), stdout=True)
            self.logger.closeLogger()
        return 0

    def start_recording_thread(self):
        self.manager_thread = threading.Thread(target=self.start_recording)
        self.manager_running = True
        self.manager_thread.thread_running = True
        self.manager_thread.start()

    def stop_recording(self):
        self.manager_running = False
        self.manager_thread.thread_running = False
        self.manager_thread.join()

if __name__=="__main__":
    import threading
    vid_dir = "/home/niek/Videos/basler"
    size = (1936, 1216)
    rec = BaslerMouseRecorder(vid_dir, size)

    video_length = 3 # seconds
    start = time.time()
    threading.Thread(target=rec.start_recording, args=()).start() # use threading to have the recording run in the background. E.g. in a gui.
    now = start
    video_length += 1 # another second for setting everything up, so the recording time is roughly the previously given time
    while now - start < video_length:
        now = time.time()
    rec.stop_recording()
