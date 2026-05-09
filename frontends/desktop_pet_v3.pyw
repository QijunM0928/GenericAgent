#!/opt/miniforge3/bin/python3
"""Desktop Pet v3 — AVPlayer video playback with CIFilter chroma key.

Uses AVPlayer + AVPlayerLayer for native 30fps video playback.
CIFilter chroma key removes teal background in real-time on GPU.
No sprite sheet preprocessing needed — plays original mp4 files directly.
"""
import os, sys, json, random, threading, socket, time
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 51983
SKINS_DIR = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), 'skins')

# ─── Skin Loader (video-based) ────────────────────────────────────────────────

def load_skin(skin_name):
    """Load skin config for video-based playback."""
    skin_dir = os.path.join(SKINS_DIR, skin_name)
    with open(os.path.join(skin_dir, 'skin.json')) as f:
        cfg = json.load(f)
    animations = {}
    for state, anim_cfg in cfg.get('animations', {}).items():
        video_path = os.path.join(skin_dir, anim_cfg['file'])
        animations[state] = {
            'file': video_path,
            'loop': anim_cfg.get('loop', True),
            'fps': anim_cfg.get('fps', 30),
        }
    size = cfg.get('size', {})
    return {
        'name': cfg.get('name', skin_name),
        'width': size.get('width', 250),
        'height': size.get('height', 250),
        'animations': animations,
    }

def discover_skins():
    skins = []
    if os.path.isdir(SKINS_DIR):
        for d in sorted(os.listdir(SKINS_DIR)):
            if os.path.isfile(os.path.join(SKINS_DIR, d, 'skin.json')):
                skins.append(d)
    return skins

# ─── Behavior Constants ────────────────────────────────────────────────────────

LOOP_STATES = {'idle', 'walk', 'run', 'sprint', 'fly'}
EMOTION_STATES = {'angry', 'happy', 'sad', 'surprise', 'bow', 'jump'}

MOVE_SPEEDS = {'walk': 1, 'run': 2, 'sprint': 3, 'fly': 1}
MOVE_DURATIONS = {'walk': (4, 8), 'run': (3, 5), 'sprint': (1, 3), 'fly': (3, 6)}

BEHAVIOR_PROBS = [('idle', 0.7), ('move', 0.85), ('emotion', 1.0)]
BEHAVIOR_DELAYS = {
    'idle': (30, 60),
    'move': (20, 40),
    'emotion': (25, 50),
    'sprint': (20, 40),
}

# ─── macOS Pet (AVPlayer + CIFilter) ──────────────────────────────────────────

import objc
from AppKit import (
    NSApplication, NSApp, NSWindow, NSView,
    NSColor, NSBackingStoreBuffered, NSFloatingWindowLevel,
    NSMakePoint, NSMakeRect, NSMakeSize, NSMenu, NSMenuItem,
    NSEvent,
)
from Quartz import (
    CGMainDisplayID, CGDisplayPixelsWide, CGDisplayPixelsHigh,
    CIFilter, kCIInputImageKey,
    kCVPixelBufferPixelFormatTypeKey, kCVPixelFormatType_32BGRA,
)
from AVFoundation import (
    AVPlayer, AVPlayerItem, AVURLAsset,
    AVPlayerLayer, AVVideoComposition,
    AVLayerVideoGravityResizeAspect,
)
from CoreMedia import CMTimeMake, CMTimeGetSeconds, kCMTimeZero
from Foundation import (
    NSObject, NSRunLoop, NSDate, NSTimer, NSURL,
    NSNotificationCenter, NSData,
)

# ─── Chroma Key via CIColorCube ─────────────────────────────────────────────

def build_chroma_key_cube_data(dimension=32):
    """Build CIColorCube LUT that maps teal background → transparent.

    Uses two-zone approach:
    - Core zone (hard match): alpha = 0
    - Soft zone (near-match): alpha fades from 0 → 1 over a transition band

    This eliminates residual green fringe while keeping soft edges on the character.
    Uses premultiplied alpha as required by CIColorCube.
    """
    import numpy as np
    cube = np.zeros((dimension ** 3, 4), dtype=np.float32)
    idx = 0
    for b in range(dimension):
        bf = b / (dimension - 1)
        for g in range(dimension):
            gf = g / (dimension - 1)
            for r in range(dimension):
                rf = r / (dimension - 1)
                # Teal-ness score: how much this pixel looks like the background
                # Background is approximately R≈0.15, G≈0.63, B≈0.60 (teal/cyan)
                # Core: R < 0.45, G > 0.40, B > 0.40, G+B high, R low relative to G+B
                r_low = max(0, 1.0 - rf / 0.45)          # 1 when R=0, 0 when R≥0.45
                g_high = min(1.0, gf / 0.40)              # 0 when G≤0, 1 when G≥0.40
                b_high = min(1.0, bf / 0.40)              # 0 when B≤0, 1 when B≥0.40
                gb_close = 1.0 - min(1.0, abs(gf - bf) / 0.30)  # 1 when G≈B, 0 when |G-B|≥0.30
                brightness = (gf + bf) / 2.0
                bright_score = min(1.0, brightness / 0.40)  # G+B must be reasonably high

                teal_score = r_low * g_high * b_high * gb_close * bright_score

                # Core zone: teal_score > 0.5 → fully transparent
                # Soft zone: 0.2 < teal_score < 0.5 → gradual fade + desaturate
                # Outside: teal_score < 0.2 → fully opaque
                if teal_score > 0.5:
                    alpha = 0.0
                    out_r, out_g, out_b = 0.0, 0.0, 0.0
                elif teal_score > 0.2:
                    alpha = 1.0 - (teal_score - 0.2) / 0.3  # linear fade 1→0
                    # Desaturate edge pixels so fringe is gray, not green
                    gray = 0.299 * rf + 0.587 * gf + 0.114 * bf
                    blend = (teal_score - 0.2) / 0.3  # 0 at inner edge, 1 at outer
                    out_r = rf * (1 - blend) + gray * blend
                    out_g = gf * (1 - blend) + gray * blend
                    out_b = bf * (1 - blend) + gray * blend
                else:
                    alpha = 1.0
                    out_r, out_g, out_b = rf, gf, bf

                cube[idx] = [out_r * alpha, out_g * alpha, out_b * alpha, alpha]
                idx += 1
    return cube.tobytes()


# Pre-build the cube data once at module level
_CUBE_DIM = 64
_CUBE_BYTES = build_chroma_key_cube_data(_CUBE_DIM)


class MacPet(NSObject):
    """macOS desktop pet with AVPlayer video playback and chroma key."""

    def init(self):
        self = objc.super(MacPet, self).init()
        if self is None:
            return None

        # Config
        self.skin_name = 'leo_hd'
        self.skin = load_skin(self.skin_name)
        self.available_skins = discover_skins()
        self.display_width = self.skin['width']
        self.display_height = self.skin['height']

        # Screen size
        main_id = CGMainDisplayID()
        self.screen_width = CGDisplayPixelsWide(main_id)
        self.screen_height = CGDisplayPixelsHigh(main_id)

        # State
        self.current_state = 'idle'
        self._is_dragging = False
        self._drag_offset_x = 0
        self._drag_offset_y = 0
        self._behavior_suspended = False
        self._behavior_suspend_timer = None
        self._direction = 1
        self._breath_idx = 0
        self.behavior_timer = None
        self.move_timer = None
        self._animation_timer = None
        self._toast_window = None
        self._toast_timer = None
        self._last_behavior_type = 'idle'
        self._player = None
        self._player_layer = None
        self._status_observer = None
        self._playback_observer = None
        self._sound_on = False

        # Create window
        initial_x = self.screen_width // 2
        initial_y = self.screen_height - self.display_height - 80
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(initial_x, self.screen_height - initial_y - self.display_height,
                       self.display_width, self.display_height),
            0,
            NSBackingStoreBuffered,
            False
        )
        self.window.setOpaque_(False)
        self.window.setBackgroundColor_(NSColor.clearColor())
        self.window.setHasShadow_(False)
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setMovableByWindowBackground_(False)
        self.window.setAcceptsMouseMovedEvents_(True)

        # Create content view with hitTest_
        pet_ref = self

        class PetView(NSView):
            def hitTest_(self, point):
                # Simple bounding box hit test for now
                view_point = self.convertPoint_fromView_(point, None)
                x, y = int(view_point.x), int(view_point.y)
                if 0 <= x < pet_ref.display_width and 0 <= y < pet_ref.display_height:
                    return self
                return None

            def mouseDown_(self, event):
                pet_ref._on_mouse_down(event)

            def mouseDragged_(self, event):
                pet_ref._on_mouse_dragged(event)

            def mouseUp_(self, event):
                pet_ref._on_mouse_up(event)

            def rightMouseDown_(self, event):
                pet_ref._on_right_click(event)

        self.content_view = PetView.alloc().initWithFrame_(
            NSMakeRect(0, 0, self.display_width, self.display_height)
        )

        # Setup AVPlayer layer
        self._setup_player_layer()

        self.window.setContentView_(self.content_view)
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)

        # Start with idle
        self.set_state('idle')

        # Start behavior timer
        self._schedule_next_behavior()

        # Start HTTP server
        self._start_http_server()

        return self

    # ─── AVPlayer Setup ────────────────────────────────────────────────

    def _setup_player_layer(self):
        """Create AVPlayer and AVPlayerLayer for video playback."""
        self._player = AVPlayer.alloc().init()
        self._player.setMuted_(not self._sound_on)

        self._player_layer = AVPlayerLayer.playerLayerWithPlayer_(self._player)
        self._player_layer.setFrame_(NSMakeRect(0, 0, self.display_width, self.display_height))
        self._player_layer.setVideoGravity_(AVLayerVideoGravityResizeAspect)

        # Request BGRA pixel format so alpha channel is preserved
        self._player_layer.setPixelBufferAttributes_({
            kCVPixelBufferPixelFormatTypeKey: kCVPixelFormatType_32BGRA,
        })

        self.content_view.setWantsLayer_(True)
        self.content_view.layer().addSublayer_(self._player_layer)

    def _make_chroma_key_filter(self):
        """Create a fresh CIColorCube filter for one composition."""
        ns_data = NSData.dataWithBytes_length_(_CUBE_BYTES, len(_CUBE_BYTES))
        filt = CIFilter.filterWithName_('CIColorCube')
        filt.setDefaults()
        filt.setValue_forKey_(_CUBE_DIM, 'inputCubeDimension')
        filt.setValue_forKey_(ns_data, 'inputCubeData')
        return filt

    def _play_video(self, state):
        """Start playing the video for the given state with chroma key."""
        anim = self.skin['animations'].get(state)
        if not anim:
            return

        video_path = anim['file']
        if not os.path.exists(video_path):
            return

        url = NSURL.fileURLWithPath_(video_path)
        asset = AVURLAsset.URLAssetWithURL_options_(url, None)
        item = AVPlayerItem.playerItemWithAsset_(asset)

        # Apply chroma key via AVVideoComposition + CIFilter handler
        # This is the Apple-recommended way to apply CIFilter to video playback
        chroma_filter = self._make_chroma_key_filter()

        def filter_handler(request):
            source = request.sourceImage().imageByClampingToExtent()
            chroma_filter.setValue_forKey_(source, kCIInputImageKey)
            output = chroma_filter.outputImage().imageByCroppingToRect_(
                request.sourceImage().extent()
            )
            request.finishWithImage_context_(output, None)

        composition = AVVideoComposition.videoCompositionWithAsset_applyingCIFiltersWithHandler_(
            asset, filter_handler
        )
        item.setVideoComposition_(composition)

        # Handle looping / non-looping
        if anim['loop']:
            NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
                self, 'itemDidLoop:', 'AVPlayerItemDidPlayToEndTimeNotification', item
            )
        else:
            NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
                self, 'itemDidEnd:', 'AVPlayerItemDidPlayToEndTimeNotification', item
            )

        self._player.replaceCurrentItemWithPlayerItem_(item)
        self._player.play()

    def itemDidEnd_(self, notification):
        """Non-looping animation finished → return to idle."""
        if self.current_state != 'idle':
            self.set_state('idle')

    def itemDidLoop_(self, notification):
        """Looping animation reached end → seek back to start."""
        self._player.seekToTime_(kCMTimeZero)

    def _stop_video(self):
        """Stop current video playback."""
        if self._player:
            self._player.pause()

    # ─── State Management ──────────────────────────────────────────────

    def set_state(self, state):
        if state not in self.skin['animations']:
            return
        if self._animation_timer and self._animation_timer.isValid():
            self._animation_timer.invalidate()
        if self._playback_observer:
            self._player.removeTimeObserver_(self._playback_observer)
            self._playback_observer = None
        self.current_state = state
        self._play_video(state)

    # ─── Mouse Events ──────────────────────────────────────────────────

    def _on_mouse_down(self, event):
        if event.clickCount() >= 2:
            available = [s for s in EMOTION_STATES if s in self.skin['animations']]
            if available:
                self.set_state(random.choice(available))
            self._is_dragging = False
            return
        self._is_dragging = True
        win_frame = self.window.frame()
        mouse_screen = NSEvent.mouseLocation()
        self._drag_offset_x = win_frame.origin.x - mouse_screen.x
        self._drag_offset_y = win_frame.origin.y - mouse_screen.y
        self._suspend_behavior()

    def _on_mouse_dragged(self, event):
        if not self._is_dragging:
            return
        mouse_screen = NSEvent.mouseLocation()
        new_x = mouse_screen.x + self._drag_offset_x
        new_y = mouse_screen.y + self._drag_offset_y
        self.window.setFrameOrigin_(NSMakePoint(new_x, new_y))

    def _on_mouse_up(self, event):
        self._is_dragging = False
        self._behavior_suspended = True
        if self._behavior_suspend_timer and self._behavior_suspend_timer.isValid():
            self._behavior_suspend_timer.invalidate()
        self._behavior_suspend_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            2.0, self, 'resumeBehavior:', None, False
        )

    def _on_right_click(self, event):
        menu = NSMenu.alloc().init()

        skin_menu = NSMenu.alloc().init()
        skin_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Skins', None, '')
        skin_item.setSubmenu_(skin_menu)
        for sname in self.available_skins:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(sname, 'changeSkin:', '')
            item.setTarget_(self)
            item.setRepresentedObject_(sname)
            if sname == self.skin_name:
                item.setState_(1)
            skin_menu.addItem_(item)
        menu.addItem_(skin_item)

        state_menu = NSMenu.alloc().init()
        state_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Animation', None, '')
        state_item.setSubmenu_(state_menu)
        for sname in self.skin['animations']:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(sname, 'changeState:', '')
            item.setTarget_(self)
            item.setRepresentedObject_(sname)
            if sname == self.current_state:
                item.setState_(1)
            state_menu.addItem_(item)
        menu.addItem_(state_item)

        menu.addItem_(NSMenuItem.separatorItem())
        sound_title = 'Sound: ON' if self._sound_on else 'Sound: OFF'
        sound_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(sound_title, 'toggleSound:', '')
        sound_item.setTarget_(self)
        menu.addItem_(sound_item)

        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_('Quit', 'terminate:', '')
        menu.addItem_(quit_item)

        NSApp.activateIgnoringOtherApps_(True)
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self.content_view)

    def changeSkin_(self, sender):
        new_skin = sender.representedObject()
        if new_skin == self.skin_name:
            return
        self.skin_name = new_skin
        self.skin = load_skin(new_skin)
        self.display_width = self.skin['width']
        self.display_height = self.skin['height']
        self.current_state = 'idle'
        win_frame = self.window.frame()
        self.window.setFrame_display_(
            NSMakeRect(win_frame.origin.x, win_frame.origin.y,
                       self.display_width, self.display_height), True)
        self._player_layer.setFrame_(NSMakeRect(0, 0, self.display_width, self.display_height))
        self.content_view.setFrame_(NSMakeRect(0, 0, self.display_width, self.display_height))
        self._play_video('idle')

    def changeState_(self, sender):
        self.set_state(sender.representedObject())

    def toggleSound_(self, sender):
        self._sound_on = not self._sound_on
        self._player.setMuted_(not self._sound_on)

    # ─── Behavior ──────────────────────────────────────────────────────

    def _schedule_next_behavior(self):
        lo, hi = BEHAVIOR_DELAYS.get(self._last_behavior_type, (30, 60))
        delay = random.uniform(lo, hi)
        self.behavior_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            delay, self, 'triggerBehavior:', None, False
        )

    def triggerBehavior_(self, timer):
        if self._behavior_suspended:
            self._schedule_next_behavior()
            return

        states = list(self.skin['animations'].keys())
        loop_states = [s for s in states if self.skin['animations'][s].get('loop', True) and s != 'idle']
        move_states = [s for s in loop_states if s in MOVE_SPEEDS]
        emotion_states = [s for s in states if s in EMOTION_STATES]

        roll = random.random()
        if roll < 0.7:
            self.set_state('idle')
            self._last_behavior_type = 'idle'
        elif roll < 0.85 and move_states:
            state = random.choice(move_states)
            self.set_state(state)
            self._direction = random.choice([-1, 1])
            self._start_moving(state)
            self._last_behavior_type = 'move'
        elif emotion_states:
            self.set_state(random.choice(emotion_states))
            self._last_behavior_type = 'emotion'
        else:
            self.set_state('idle')
            self._last_behavior_type = 'idle'

        self._schedule_next_behavior()

    def _start_moving(self, state):
        if self.move_timer and self.move_timer.isValid():
            self.move_timer.invalidate()
        self.move_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.05, self, 'moveStep:', None, True
        )
        lo, hi = MOVE_DURATIONS.get(state, (2, 5))
        duration = random.uniform(lo, hi)
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            duration, self, 'stopMoving:', None, False
        )

    def moveStep_(self, timer):
        if self._behavior_suspended:
            return
        win_frame = self.window.frame()
        speed = MOVE_SPEEDS.get(self.current_state, 1)
        new_x = win_frame.origin.x + self._direction * speed
        if new_x < 0 or new_x + self.display_width > self.screen_width:
            self._direction *= -1
            new_x = win_frame.origin.x + self._direction * speed
        self.window.setFrameOrigin_(NSMakePoint(new_x, win_frame.origin.y))

    def stopMoving_(self, timer):
        if self.move_timer and self.move_timer.isValid():
            self.move_timer.invalidate()
        self.move_timer = None
        if self.current_state in MOVE_SPEEDS:
            self.set_state('idle')

    def _suspend_behavior(self):
        self._behavior_suspended = True
        if self.move_timer and self.move_timer.isValid():
            self.move_timer.invalidate()
            self.move_timer = None
        if self._behavior_suspend_timer and self._behavior_suspend_timer.isValid():
            self._behavior_suspend_timer.invalidate()
        self._behavior_suspend_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            2.0, self, 'resumeBehavior:', None, False
        )

    def resumeBehavior_(self, timer):
        self._behavior_suspended = False
        self._behavior_suspend_timer = None

    # ─── Toast ─────────────────────────────────────────────────────────

    def show_toast_safe(self, msg):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            'showToastDirect:', msg, False
        )

    def showToastDirect_(self, msg):
        self._show_toast(msg)

    def _show_toast(self, message):
        if self._toast_window:
            self._toast_window.orderOut_(None)
            self._toast_window = None
        if self._toast_timer and self._toast_timer.isValid():
            self._toast_timer.invalidate()

        from AppKit import NSTextField, NSAttributedString, NSFont, NSFontAttributeName
        font = NSFont.systemFontOfSize_(13)
        attrs = {NSFontAttributeName: font}
        attr_str = NSAttributedString.alloc().initWithString_attributes_(message, attrs)
        text_size = attr_str.size()
        tw = max(int(text_size.width) + 24, 60)
        th = max(int(text_size.height) + 16, 30)

        win_frame = self.window.frame()
        tx = win_frame.origin.x + (self.display_width - tw) // 2
        ty = win_frame.origin.y + self.display_height + 5

        self._toast_window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(tx, ty, tw, th), 0, NSBackingStoreBuffered, False
        )
        self._toast_window.setOpaque_(False)
        self._toast_window.setBackgroundColor_(NSColor.clearColor())
        self._toast_window.setLevel_(NSFloatingWindowLevel)

        from AppKit import NSBezierPath
        class ToastView(NSView):
            def drawRect_(self, rect):
                bg = NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.99, 0.88, 0.95)
                border = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.6, 0.6, 0.6, 0.8)
                path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, 8, 8)
                bg.setFill()
                path.fill()
                border.setStroke()
                path.setLineWidth_(1)
                path.stroke()

        tv = ToastView.alloc().initWithFrame_(NSMakeRect(0, 0, tw, th))
        label = NSTextField.alloc().initWithFrame_(NSMakeRect(8, 4, tw - 16, th - 8))
        label.setStringValue_(message)
        label.setFont_(font)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        tv.addSubview_(label)
        self._toast_window.setContentView_(tv)
        self._toast_window.orderFront_(None)

        self._toast_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            3.0, self, 'dismissToast:', None, False
        )

    def dismissToast_(self, timer):
        if self._toast_window:
            self._toast_window.orderOut_(None)
            self._toast_window = None
        self._toast_timer = None

    # ─── HTTP Server ────────────────────────────────────────────────────

    def _start_http_server(self):
        pet = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query)
                if 'state' in params:
                    pet.set_state_safe(params['state'][0])
                    self._respond(200, b'ok')
                elif 'status' in params:
                    info = {'state': pet.current_state, 'skin': pet.skin_name, 'direction': pet._direction}
                    self._respond(200, json.dumps(info).encode())
                elif 'msg' in params:
                    pet.show_toast_safe(params['msg'][0])
                    self._respond(200, b'ok')
                else:
                    self._respond(400, b'?state=idle/walk/fly&msg=hello')

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get('Content-Length', 0))).decode()
                if body:
                    pet.show_toast_safe(body)
                    self._respond(200, b'ok')
                else:
                    self._respond(400, b'empty body')

            def _respond(self, code, data):
                self.send_response(code)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        HTTPServer.allow_reuse_address = True
        try:
            srv = HTTPServer(('127.0.0.1', PORT), Handler)
            t = threading.Thread(target=srv.serve_forever, daemon=True)
            t.start()
            print(f'Pet HTTP server: http://127.0.0.1:{PORT}/?msg=hello')
        except OSError:
            print(f'Port {PORT} in use, HTTP server not started')

    def set_state_safe(self, state):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            'setStateDirect:', state, False
        )

    def setStateDirect_(self, state):
        self.set_state(state)

    # ─── Run Loop ──────────────────────────────────────────────────────

    def run(self):
        import signal
        self._sigint_received = False
        signal.signal(signal.SIGINT, lambda s, f: self._handle_sigint())

        def check_sigint(timer):
            if self._sigint_received:
                NSApp.terminate_(None)

        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, self, 'checkSigint:', None, True
        )

        app = NSApplication.sharedApplication()
        app.setDelegate_(self)
        app.run()

    def _handle_sigint(self):
        self._sigint_received = True

    def checkSigint_(self, timer):
        if self._sigint_received:
            NSApp.terminate_(None)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    _s = socket.socket()
    try:
        _s.connect(('127.0.0.1', PORT))
        _s.close()
        print(f'Pet already running on port {PORT}, exiting.')
        sys.exit(0)
    except ConnectionRefusedError:
        pass

    app = NSApplication.sharedApplication()
    pet = MacPet.alloc().init()
    pet.run()
