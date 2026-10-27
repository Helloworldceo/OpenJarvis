"""Floating "Hey Jarvis" orb for macOS.

A small circle that floats above every window and every Space. Drag it
anywhere; click it to talk (or to interrupt a reply); right-click to mute or
quit. Say "Hey Jarvis" to start a conversation hands-free.

Run with ``python -m openjarvis.orb`` while the Jarvis server is running.
"""

from __future__ import annotations

import math
import sys

import objc
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSColor,
    NSEvent,
    NSGradient,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSPanel,
    NSScreen,
    NSStatusWindowLevel,
    NSView,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorStationary,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskNonactivatingPanel,
)
from Foundation import NSObject, NSTimer, NSUserDefaults
from PyObjCTools import AppHelper

from openjarvis.orb.voice import OrbState, VoiceLoop, default_client

SIZE = 64  # orb diameter in points
PAD = 14  # room for the glow around it
_POS_KEY = "JarvisOrbOrigin"

# Base colours per phase (r, g, b).
COLORS = {
    "idle": (0.13, 0.83, 0.93),
    "listening": (0.20, 0.90, 0.55),
    "thinking": (0.60, 0.45, 0.98),
    "speaking": (0.13, 0.83, 0.93),
    "muted": (0.45, 0.47, 0.50),
    "error": (0.95, 0.35, 0.35),
}

TIPS = {
    "idle": 'Say "Hey Jarvis" or click to talk',
    "listening": "Listening… pause when you're done",
    "thinking": "Thinking…",
    "speaking": "Speaking… click to interrupt",
    "muted": "Muted — right-click to unmute",
    "error": "Something went wrong — is the Jarvis server running?",
}


def _rgb(c, alpha=1.0):
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(c[0], c[1], c[2], alpha)


class OrbView(NSView):
    def initWithFrame_(self, frame):
        self = objc.super(OrbView, self).initWithFrame_(frame)
        if self is None:
            return None
        self.phase = "idle"
        self.level = 0.0
        self.t = 0.0
        self.loop = None
        self.down_at = None
        self.moved = False
        return self

    def acceptsFirstMouse_(self, _event):
        return True

    def tick_(self, _timer):
        self.t += 1 / 30
        self.setNeedsDisplay_(True)

    def drawRect_(self, _rect):
        c = COLORS.get(self.phase, COLORS["idle"])
        cx = cy = (SIZE + 2 * PAD) / 2
        r = SIZE / 2
        t = self.t
        if self.phase == "listening":
            r *= 1 + 0.18 * self.level
        elif self.phase == "idle":
            r *= 1 + 0.03 * math.sin(t * 1.6)
        elif self.phase == "speaking":
            r *= 1 + 0.06 * math.sin(t * 9)

        # Soft glow.
        glow_r = r + PAD * (0.6 + 0.4 * math.sin(t * 2.2))
        glow = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - glow_r, cy - glow_r, 2 * glow_r, 2 * glow_r)
        )
        _rgb(c, 0.18 if self.phase != "muted" else 0.06).set()
        glow.fill()

        # Body with a radial highlight.
        body = NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - r, cy - r, 2 * r, 2 * r)
        )
        light = (min(1, c[0] + 0.45), min(1, c[1] + 0.45), min(1, c[2] + 0.45))
        NSGradient.alloc().initWithStartingColor_endingColor_(
            _rgb(light), _rgb(c)
        ).drawInBezierPath_relativeCenterPosition_(body, (-0.35, 0.4))

        # Thinking: a spinning arc around the orb.
        if self.phase == "thinking":
            arc = NSBezierPath.bezierPath()
            start = (t * 360) % 360
            arc.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
                (cx, cy), r + 5, start, start + 110
            )
            arc.setLineWidth_(3)
            _rgb((1, 1, 1), 0.85).set()
            arc.stroke()

        # Speaking: outward ripple.
        if self.phase == "speaking":
            k = (t * 1.2) % 1.0
            ring_r = r + 4 + k * PAD
            ring = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx - ring_r, cy - ring_r, 2 * ring_r, 2 * ring_r)
            )
            ring.setLineWidth_(2)
            _rgb(c, 0.8 * (1 - k)).set()
            ring.stroke()

    # Click vs drag: move the window on drag, treat a still click as a tap.
    def mouseDown_(self, event):
        self.down_at = NSEvent.mouseLocation()
        self.origin = self.window().frame().origin
        self.moved = False

    def mouseDragged_(self, _event):
        p = NSEvent.mouseLocation()
        dx, dy = p.x - self.down_at.x, p.y - self.down_at.y
        if abs(dx) + abs(dy) > 3:
            self.moved = True
        self.window().setFrameOrigin_((self.origin.x + dx, self.origin.y + dy))

    def mouseUp_(self, _event):
        if self.moved:
            o = self.window().frame().origin
            NSUserDefaults.standardUserDefaults().setObject_forKey_(
                [o.x, o.y], _POS_KEY
            )
        elif self.loop is not None:
            self.loop.tap()

    def rightMouseDown_(self, event):
        menu = NSMenu.alloc().initWithTitle_("Jarvis")
        muted = self.phase == "muted"
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Unmute" if muted else "Mute", "toggleMute:", ""
        )
        item.setTarget_(self)
        menu.addItem_(item)
        menu.addItem_(NSMenuItem.separatorItem())
        quit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
            "Quit Jarvis Orb", "terminate:", "q"
        )
        menu.addItem_(quit_item)
        NSMenu.popUpContextMenu_withEvent_forView_(menu, event, self)

    def toggleMute_(self, _sender):
        if self.loop is not None:
            self.loop.set_muted(self.phase != "muted")


class AppDelegate(NSObject):
    def applicationDidFinishLaunching_(self, _note):
        side = SIZE + 2 * PAD
        screen = NSScreen.mainScreen().visibleFrame()
        saved = NSUserDefaults.standardUserDefaults().objectForKey_(_POS_KEY)
        if saved:
            x, y = float(saved[0]), float(saved[1])
        else:
            x = screen.origin.x + screen.size.width - side - 24
            y = screen.origin.y + 120

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, side, side),
            NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
            NSBackingStoreBuffered,
            False,
        )
        # setFloatingPanel_ resets the level, so set it first.
        panel.setFloatingPanel_(True)
        panel.setLevel_(NSStatusWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(False)
        panel.setHidesOnDeactivate_(False)

        view = OrbView.alloc().initWithFrame_(NSMakeRect(0, 0, side, side))
        view.setToolTip_(TIPS["idle"])
        panel.setContentView_(view)
        panel.orderFrontRegardless()
        self.panel, self.view = panel, view

        schedule = (
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_
        )  # noqa: E501
        self.timer = schedule(1 / 30, view, "tick:", None, True)

        def on_change(state: OrbState) -> None:
            phase, level, message = state.phase, state.level, state.message
            AppHelper.callAfter(self.apply, phase, level, message)

        self.loop = VoiceLoop(default_client(), on_change)
        view.loop = self.loop
        self.loop.start()

    @objc.python_method
    def apply(self, phase, level, message):
        self.view.phase = phase
        self.view.level = level
        tip = TIPS.get(phase, "")
        if phase == "error" and message:
            tip = f"{tip}\n{message}"
        self.view.setToolTip_(tip)


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("The Jarvis orb currently runs on macOS only.")
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    delegate = AppDelegate.alloc().init()
    app.setDelegate_(delegate)
    NSApp.activateIgnoringOtherApps_(False)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
