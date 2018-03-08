#!/usr/bin/env python
# -*- coding: utf-8 -*-
import signal
import sys
import time

import logging
logger = logging.getLogger('qrcode_generator')

import gi
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst
GObject.threads_init()
Gst.init(None)

import easyevent
from gstmanager import PipelineManager


class QrLipsyncGenerator(easyevent.User):
    '''
        Generate video with qrcode incrusted using gstreamer
        and each seconde, there is an audio beep at different frequency
    '''
    def __init__(self, settings, mainloop):
        easyevent.User.__init__(self)
        signal.signal(signal.SIGINT, self._signal_handler)
        self.register_event("eos")
        self.settings = settings
        self.mainloop = mainloop
        #self.duration = (settings.get('duration') - 1) * Gst.SECOND
        self.delay_audio_freq_change = settings.get('delay_audio_freq_change') * Gst.SECOND
        self.freq_array = settings.get('freq_array')
        self.output_file = settings.get('output_file')

        self.start_time = None
        self.end_time = None
        self.id_prob_audio_src = None
        self.audio_src_pad = None
        self.current_audio_timestamp = 0
        self.increment = 0

        self.pipeline_str = self._get_pipeline_string()
        logger.info(self.pipeline_str)
        self.pipeline = PipelineManager(self.pipeline_str)

    def _signal_handler(self, signal, frame):
        logger.info('You pressed Ctrl+C!')
        sys.exit(0)

    def _get_pipeline_string(self):
        s = self.settings
        # Black background
        video_src = "videotestsrc pattern=%s num-buffers=%s" % (s['background'], s['framerate'] * s['duration'])
        video_caps = "video/x-raw, format=(string)I420, width=(int)%s, height=(int)%s, framerate=(fraction)%s/1" % (s.get('width', 320), s.get('height', 240), s.get('framerate', 30))
        # the ticks duration is samplesperbuffer-long, so we need 1s long samples
        audio_src = "audiotestsrc wave=8 freq=%s samplesperbuffer=%s name=audio_src num-buffers=%s" % (self.freq_array[self.increment], s['samplerate'], s['duration'])
        audio_caps = 'capsfilter caps="audio/x-raw, format=(string)S16LE, layout=(string)interleaved, rate=(int)%s, channels=(int)1"' % s['samplerate']
        qroverlay = self._get_qroverlay(self.freq_array)
        textoverlay = self._get_textoverlay()
        video_converter = "videoconvert"
        video_encoder = s['vcodec']
        self.increment += 1
        muxer = "%s name=mux" % s['muxer']
        sink = "filesink location=%s" % self.output_file
        pipeline = ' ! '.join([video_src, video_caps, qroverlay, textoverlay, video_converter, video_encoder, muxer, sink])
        if not s["disable_audio"]:
            pipeline += " " + " ! ".join([audio_src, audio_caps, s['acodec'], 'mux.'])
        return pipeline

    def _get_textoverlay(self):
        if self.settings.get('enable_textoverlay', True):
            return 'timeoverlay text=%s halignment=center valignment=bottom font-desc="Arial 30"' % self.settings.get('qrname')
        return ""

    def _get_qroverlay(self, data_array):
        s = self.settings
        extra_data_array = ",".join([str(i) for i in data_array])
        plugin_name = s.get('qrname', 'myqroverlay')
        x_position = 50
        y_position = 20
        error_correction = 3
        span_buffer = 1
        interval_buffers = self.settings["framerate"]
        pixel_size = s.get('qr_pix_size', 2)
        if not self.settings['disable_audio']:
            data_name = s.get('extra_data_name', 'tickfreq')
            qroverlay = 'qroverlay x=%s y=%s name=%s qrcode-error-correction=%s extra-data-span-buffers=%s extra-data-interval-buffers=%s extra-data-name=%s extra-data-array="%s" pixel-size=%s' % (x_position, y_position, plugin_name, error_correction, span_buffer, interval_buffers, data_name, extra_data_array, pixel_size)
        else:
            qroverlay = 'qroverlay x=%s y=%s name=%s qrcode-error-correction=%s extra-data-span-buffers=%s pixel-size=%s' % (x_position, y_position, plugin_name, error_correction, span_buffer, pixel_size)

        return qroverlay

    def on_audio_src_buffer(self, pad, info, data):
        buf = info.get_buffer()
        self.current_audio_timestamp = buf.pts
        self.update_audiotestsrc_freq()
        return True

    def update_audiotestsrc_freq(self):
        #if self.current_audio_timestamp >= self.duration * Gst.NSECOND:
        #    self.disconnect_probes()
        if self.current_audio_timestamp % self.delay_audio_freq_change == 0:
            self.set_audiotestsrc_freq(self.freq_array[self.increment])
            self.increment += 1
            if self.increment == len(self.freq_array):
                self.increment = 0

    def set_audiotestsrc_freq(self, freq):
        audio_src = self.pipeline.pipeline.get_by_name("audio_src")
        logger.info("Change audiotestsrc frequency to %s Hz" % freq)
        audio_src.set_property('freq', freq)

    def disconnect_probes(self):
        logger.debug('Disconnecting probes')
        if self.audio_src_pad:
            self.audio_src_pad.remove_probe(self.id_prob_audio_src)

    def start(self):
        self.start_time = time.time()
        if not self.settings['disable_audio']:
            audio_src_elt = self.pipeline.pipeline.get_by_name("audio_src")
            self.audio_src_pad = audio_src_elt.get_static_pad('src')
            self.id_prob_audio_src = self.audio_src_pad.add_probe(Gst.PadProbeType.BUFFER, self.on_audio_src_buffer, None)
        self.pipeline.run()

    def evt_eos(self, event):
        self.end_time = time.time()
        self.unregister_event("eos")
        self.pipeline.stop()
        render_duration = self.end_time - self.start_time
        fps = self.settings['framerate'] * self.settings['duration'] / render_duration
        logger.info("Rendering of %s took %.2fs (%i fps)" % (self.output_file, render_duration, fps))
        GObject.idle_add(self.mainloop.quit)


if __name__ == '__main__':

    import argparse
    parser = argparse.ArgumentParser(
        description='Generate videos suitable for measuring lipsync with qrcodes',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument('-v', '--verbosity', help='increase output verbosity', action="store_true")
    parser.add_argument('-a', '--disable-audio', help='enable audio track', action="store_true", default=False)
    parser.add_argument('-t', '--enable-textoverlay', help='enable text overlay (shows timecode)', action="store_true", default=True)
    parser.add_argument('-q', '--qrcode-name', help='name inserted into the qrcode pattern', default='cam1')
    parser.add_argument('-d', '--duration', help='duration in seconds', type=int, default=30)
    parser.add_argument('-r', '--framerate', help='framerate', type=int, default=60)
    parser.add_argument('-s', '--size', help='video size', type=str, default="640x360")
    parser.add_argument('-f', '--format', help='video format: qt/h264/pcm (default) or mp4/h264/aac', choices=['mp4', 'qt'], default='qt')
    parser.add_argument('-b', '--background', help='background color', choices=['snow', 'black', 'white', 'red', 'green', 'blue', 'smpte', 'pinwheel'], default='blue')

    options = parser.parse_args()

    verbosity = getattr(logging, "DEBUG" if options.verbosity else "INFO")
    logging.basicConfig(
        level=verbosity,
        format='%(asctime)s %(name)-12s %(levelname)-8s %(message)s',
        stream=sys.stderr
    )

    # Name that will identify the qrcode
    qrname = options.qrcode_name
    try:
        width, height = options.size.split("x")
    except ValueError:
        logger.error('Size must be in the following format: "640x360"')
        sys.exit(1)

    settings = {
        "disable_audio": options.disable_audio,
        "samplerate": 48000,
        "duration": options.duration,
        "delay_audio_freq_change": 1,
        "qrname": qrname,
        "format": options.format,
        "width": width,
        "height": height,
        "framerate": options.framerate,
        "qr_pix_size": 4,
        "extra_data_name": "tickfreq",
        "freq_array": (240, 480, 720, 960, 1200, 1440, 1680, 1920, 2160, 2400, 2640, 2880, 3120, 3360, 3600, 3840, 4080, 4320, 4560, 4800, 5040, 5280, 5520, 5760, 6000, 6240, 6480, 6720, 6960, 7200, 7440, 7680, 7920, 8160, 8400, 8640, 8880, 9120, 9360, 9600, 9840, 10080),
        "background": options.background,
        "enable_textoverlay": True,
    }

    video_format = options.format
    if video_format == "qt":
        settings["muxer"] = "qtmux"
        settings["vcodec"] = "x264enc pass=5 quantizer=21 tune=zerolatency"
        settings["acodec"] = "identity"
        settings["fileext"] = ".qt"
        settings["output_file"] = "%s-qrcode.qt" % qrname
    elif video_format == "mp4":
        settings["muxer"] = "mp4mux"
        settings["vcodec"] = "x264enc pass=5 quantizer=21"
        settings["acodec"] = "fdkaacenc"
        settings["output_file"] = "%s-qrcode.mp4" % qrname

    ml = GObject.MainLoop()
    qr_gen = QrLipsyncGenerator(settings, ml)
    GObject.idle_add(qr_gen.start)
    ml.run()
