"""Native macOS titlebar toolbar backed by the existing Qt actions."""

import sys
from ctypes import c_void_p

if sys.platform == "darwin":
    import AppKit
    import objc
    from diffly.gui.icons import native_icon_png

    def _ns_color(hex_color):
        value = hex_color.lstrip("#")
        return AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(
            int(value[0:2], 16) / 255,
            int(value[2:4], 16) / 255,
            int(value[4:6], 16) / 255,
            1,
        )

    class _DifflyToolbarDelegate(AppKit.NSObject):
        def toolbarAllowedItemIdentifiers_(self, toolbar):
            return list(self.identifiers)

        def toolbarDefaultItemIdentifiers_(self, toolbar):
            return list(self.identifiers)

        def toolbar_itemForItemIdentifier_willBeInsertedIntoToolbar_(self, toolbar, identifier, inserted):
            action = self.actions[identifier]
            item = AppKit.NSToolbarItem.alloc().initWithItemIdentifier_(identifier)
            item.setLabel_(action.text())
            item.setPaletteLabel_(action.text())
            item.setToolTip_(action.toolTip())
            png = native_icon_png(self.symbols[identifier])
            data = AppKit.NSData.dataWithBytes_length_(png, len(png))
            image = AppKit.NSImage.alloc().initWithData_(data)
            if image is not None:
                image.setSize_(AppKit.NSMakeSize(24, 24))
                image.setTemplate_(True)
            button = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 34, 30))
            button.setTitle_("")
            button.setToolTip_(action.toolTip())
            button.setButtonType_(AppKit.NSButtonTypeMomentaryPushIn)
            button.setBordered_(False)
            button.setWantsLayer_(True)
            button.layer().setCornerRadius_(6.0)
            button.layer().setBorderWidth_(0.0)
            button.setImagePosition_(AppKit.NSImageOnly)
            button.setImageScaling_(AppKit.NSImageScaleProportionallyDown)
            if image is not None:
                button.setImage_(image)
            button.setContentTintColor_(self.normal_tint)
            button.setIdentifier_(identifier)
            button.setTarget_(self)
            button.setAction_("activate:")
            button.setEnabled_(action.isEnabled())
            tracking = AppKit.NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                AppKit.NSZeroRect,
                AppKit.NSTrackingMouseEnteredAndExited
                | AppKit.NSTrackingActiveInActiveApp
                | AppKit.NSTrackingInVisibleRect,
                self,
                {"identifier": identifier},
            )
            button.addTrackingArea_(tracking)
            item.setView_(button)
            item.setMinSize_(AppKit.NSMakeSize(34, 30))
            item.setMaxSize_(AppKit.NSMakeSize(34, 30))
            item.setEnabled_(action.isEnabled())
            self.buttons[identifier] = button
            self.items[identifier] = item
            return item

        def activate_(self, sender):
            action = self.actions.get(sender.identifier())
            if action is not None and action.isEnabled():
                action.trigger()

        def mouseEntered_(self, event):
            identifier = event.trackingArea().userInfo()["identifier"]
            button = self.buttons.get(identifier)
            if button is not None and button.isEnabled():
                button.layer().setBorderColor_(self.border_color.CGColor())
                button.layer().setBorderWidth_(1.0)
                self.hovered.add(identifier)

        def mouseExited_(self, event):
            identifier = event.trackingArea().userInfo()["identifier"]
            button = self.buttons.get(identifier)
            if button is not None:
                button.layer().setBorderWidth_(0.0)
                self.hovered.discard(identifier)

        def validateToolbarItem_(self, item):
            action = self.actions.get(item.itemIdentifier())
            return action is not None and action.isEnabled()


class NativeToolbar:
    def __init__(self, qt_window, delegate, toolbar, native_window):
        self.qt_window = qt_window
        self.delegate = delegate
        self.toolbar = toolbar
        self.native_window = native_window
        self._suppress_mouse_up = False
        mask = AppKit.NSEventMaskLeftMouseDown | AppKit.NSEventMaskLeftMouseUp
        self._event_monitor = AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
            mask, self._handle_mouse_event
        )

    def _handle_mouse_event(self, event):
        if event.window() != self.native_window:
            return event
        if self._suppress_mouse_up and event.type() == AppKit.NSEventTypeLeftMouseUp:
            self._suppress_mouse_up = False
            return None
        if event.type() != AppKit.NSEventTypeLeftMouseDown or event.clickCount() != 2:
            return event
        point = event.locationInWindow()
        content = self.native_window.contentLayoutRect()
        if point.y <= content.origin.y + content.size.height:
            return event
        title_view = self.native_window.contentView().superview().hitTest_(point)
        # AppKit owns the titlebar and toolbar hit targets. Only consume a
        # double-click on its title area; native toolbar items keep their clicks.
        if title_view is None or "Title" not in type(title_view).__name__:
            return event
        if self.qt_window.isMaximized():
            self.qt_window.showNormal()
        else:
            self.qt_window.showMaximized()
        self._suppress_mouse_up = True
        return None

    def sync(self):
        for identifier, item in self.delegate.items.items():
            action = self.delegate.actions[identifier]
            enabled = action.isEnabled()
            item.setEnabled_(enabled)
            item.setToolTip_(action.toolTip())
            button = self.delegate.buttons[identifier]
            button.setEnabled_(enabled)
            button.setToolTip_(action.toolTip())
            if not enabled:
                button.layer().setBorderWidth_(0.0)
                self.delegate.hovered.discard(identifier)
        self.toolbar.validateVisibleItems()

    def set_theme(self, theme):
        self.delegate.normal_tint = _ns_color(theme["text"])
        self.delegate.border_color = _ns_color(theme["border"])
        for button in self.delegate.buttons.values():
            button.setContentTintColor_(self.delegate.normal_tint)
            if button.identifier() in self.delegate.hovered:
                button.layer().setBorderColor_(self.delegate.border_color.CGColor())

    def close(self):
        if self._event_monitor is not None:
            AppKit.NSEvent.removeMonitor_(self._event_monitor)
            self._event_monitor = None


def install_inline_toolbar(qt_window, action_map: dict) -> NativeToolbar | None:
    """Attach an NSToolbar inline with the title, or leave the Qt toolbar intact."""
    if sys.platform != "darwin":
        return None

    from PySide6.QtWidgets import QApplication

    if QApplication.instance().platformName() != "cocoa":
        return None

    view = objc.objc_object(c_void_p=c_void_p(int(qt_window.winId())))
    native_window = view.window()
    if native_window is None:
        return None

    delegate = _DifflyToolbarDelegate.alloc().init()
    delegate.actions = {identifier: action for identifier, (action, _symbol) in action_map.items()}
    delegate.symbols = {identifier: symbol for identifier, (_action, symbol) in action_map.items()}
    theme = qt_window.config.themes[qt_window._active_theme_name]
    delegate.normal_tint = _ns_color(theme["text"])
    delegate.border_color = _ns_color(theme["border"])
    identifiers = []
    for identifier in action_map:
        if identifiers:
            identifiers.append(AppKit.NSToolbarSpaceItemIdentifier)
        identifiers.append(identifier)
    delegate.identifiers = tuple(identifiers)
    delegate.items = {}
    delegate.buttons = {}
    delegate.hovered = set()
    toolbar = AppKit.NSToolbar.alloc().initWithIdentifier_("com.diffly.merge")
    toolbar.setDelegate_(delegate)
    toolbar.setAllowsUserCustomization_(False)
    toolbar.setDisplayMode_(AppKit.NSToolbarDisplayModeIconOnly)
    native_window.setToolbar_(toolbar)
    native_window.setToolbarStyle_(AppKit.NSWindowToolbarStyleUnifiedCompact)
    if native_window.toolbar() is None:
        return None
    bridge = NativeToolbar(qt_window, delegate, toolbar, native_window)
    bridge.sync()
    return bridge
