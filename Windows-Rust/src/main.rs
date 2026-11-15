#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

#[cfg(not(target_os = "windows"))]
compile_error!("PSX Launcher Rust can only be built for Windows.");

mod config;
mod launch;
mod processes;
mod win;

use config::{AppConfig, LauncherItem};
use processes::{inventory, target_is_running};
use std::mem::{size_of, zeroed};
use std::ptr::{null, null_mut};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};
use win::{get_x_lparam, get_y_lparam, rgb, wide};
use windows_sys::Win32::Foundation::*;
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::System::Threading::{CreateMutexW, ReleaseMutex};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    ReleaseCapture, SetCapture, TME_LEAVE, TRACKMOUSEEVENT, TrackMouseEvent,
};
use windows_sys::Win32::UI::WindowsAndMessaging::*;
use windows_sys::Win32::UI::Controls::{DRAWITEMSTRUCT, MEASUREITEMSTRUCT, ODT_MENU, ODS_SELECTED};

const APP_NAME: &str = "PSX Launcher";
const APP_VERSION: &str = "2.0.0-alpha.1";
const CLASS_NAME: &str = "JamieJets.PSXLauncherRust.Window";
const MUTEX_NAME: &str = "Local\\JamieJets.PSXLauncher";

const BAR_HEIGHT: i32 = 34;
const HANDLE_WIDTH: i32 = 34;
const SETTINGS_WIDTH: i32 = 34;
const MINI_WIDTH: i32 = 62;
const ITEM_MIN_WIDTH: i32 = 74;
const ITEM_GAP: i32 = 2;

const TIMER_STATUS: usize = 1;
const TIMER_TOPMOST: usize = 2;
const TIMER_MENU: usize = 3;
const WM_STATUS_READY: u32 = WM_APP + 1;
const WM_ACTION_READY: u32 = WM_APP + 2;
const WM_MOUSE_LEAVE: u32 = 0x02A3;

const CMD_QUIT_ITEM_BASE: u32 = 10_000;
const CMD_EDIT_INI: u32 = 20_001;
const CMD_ABOUT: u32 = 20_002;
const CMD_QUIT: u32 = 20_003;

const BG: u32 = rgb(0x17, 0x19, 0x1c);
const BORDER: u32 = rgb(0x30, 0x34, 0x3a);
const PANEL: u32 = rgb(0x22, 0x25, 0x2a);
const PANEL_HOVER: u32 = rgb(0x2d, 0x31, 0x37);
const TEXT: u32 = rgb(0xf2, 0xf3, 0xf5);
const MUTED: u32 = rgb(0x96, 0x9c, 0xa5);
const OFF_DOT: u32 = rgb(0x62, 0x68, 0x70);
const GREEN: u32 = rgb(0x54, 0xc8, 0x78);
const ORANGE: u32 = rgb(0xe7, 0xa8, 0x4b);
const RED: u32 = rgb(0xdf, 0x64, 0x64);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ButtonState {
    Off,
    Partial,
    Running,
    Error,
}

impl ButtonState {
    fn colour(self) -> u32 {
        match self {
            Self::Off => OFF_DOT,
            Self::Partial => ORANGE,
            Self::Running => GREEN,
            Self::Error => RED,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Region {
    None,
    Handle,
    Item(usize),
    Settings,
    Mini,
}

struct StatusPayload(Vec<ButtonState>);

enum ActionKind {
    Launch,
    Quit,
}

struct ActionPayload {
    index: usize,
    kind: ActionKind,
    result: Result<(), String>,
}

struct AppState {
    hwnd: HWND,
    config: AppConfig,
    statuses: Vec<ButtonState>,
    launch_locked_until: Vec<Option<Instant>>,
    action_busy: Vec<bool>,
    item_widths: Vec<i32>,
    expanded_width: i32,
    collapsed: bool,
    hover: Region,
    menu_open: bool,
    active_menu: HMENU,
    menu_anchor: RECT,
    menu_left_at: Option<Instant>,
    tracking_mouse: bool,
    dragging: bool,
    drag_moved: bool,
    drag_offset: POINT,
    drag_start: POINT,
    font: HFONT,
    status_busy: Arc<AtomicBool>,
    first_status_timer: bool,
}

impl AppState {
    fn new(config: AppConfig) -> Self {
        let item_count = config.items.len();
        Self {
            hwnd: 0,
            config,
            statuses: vec![ButtonState::Off; item_count],
            launch_locked_until: vec![None; item_count],
            action_busy: vec![false; item_count],
            item_widths: vec![ITEM_MIN_WIDTH; item_count],
            expanded_width: HANDLE_WIDTH + SETTINGS_WIDTH + 2,
            collapsed: false,
            hover: Region::None,
            menu_open: false,
            active_menu: 0,
            menu_anchor: RECT { left: 0, top: 0, right: 0, bottom: 0 },
            menu_left_at: None,
            tracking_mouse: false,
            dragging: false,
            drag_moved: false,
            drag_offset: POINT { x: 0, y: 0 },
            drag_start: POINT { x: 0, y: 0 },
            font: 0,
            status_busy: Arc::new(AtomicBool::new(false)),
            first_status_timer: true,
        }
    }

    fn initialize_window(&mut self) {
        let face = wide("Segoe UI");
        self.font = unsafe {
            CreateFontW(
                -15,
                0,
                0,
                0,
                FW_NORMAL as i32,
                0,
                0,
                0,
                DEFAULT_CHARSET as u32,
                OUT_DEFAULT_PRECIS as u32,
                CLIP_DEFAULT_PRECIS as u32,
                CLEARTYPE_QUALITY as u32,
                DEFAULT_PITCH as u32 | FF_DONTCARE as u32,
                face.as_ptr(),
            )
        };
        self.calculate_layout();
        let (x, y) = self.clamp_position(self.config.x, self.config.y, self.expanded_width, BAR_HEIGHT);
        unsafe {
            SetWindowPos(
                self.hwnd,
                if self.config.always_on_top { HWND_TOPMOST } else { HWND_NOTOPMOST },
                x,
                y,
                self.expanded_width,
                BAR_HEIGHT,
                SWP_SHOWWINDOW,
            );
            SetTimer(self.hwnd, TIMER_STATUS, 500, None);
            SetTimer(self.hwnd, TIMER_TOPMOST, 2000, None);
        }
    }

    fn calculate_layout(&mut self) {
        let hdc = unsafe { GetDC(self.hwnd) };
        if hdc == 0 {
            return;
        }
        let previous = unsafe { SelectObject(hdc, self.font) };
        for (index, item) in self.config.items.iter().enumerate() {
            let text = wide(&item.label);
            let mut size = SIZE { cx: 0, cy: 0 };
            unsafe {
                GetTextExtentPoint32W(hdc, text.as_ptr(), text.len().saturating_sub(1) as i32, &mut size);
            }
            self.item_widths[index] = ITEM_MIN_WIDTH.max(size.cx + 35);
        }
        unsafe {
            SelectObject(hdc, previous);
            ReleaseDC(self.hwnd, hdc);
        }
        self.expanded_width = HANDLE_WIDTH
            + SETTINGS_WIDTH
            + 2
            + self.item_widths.iter().sum::<i32>()
            + ITEM_GAP * self.item_widths.len().saturating_sub(1) as i32;
    }

    fn window_width(&self) -> i32 {
        if self.collapsed { MINI_WIDTH } else { self.expanded_width }
    }

    fn region_at(&self, x: i32, y: i32) -> Region {
        if y < 0 || y >= BAR_HEIGHT || x < 0 || x >= self.window_width() {
            return Region::None;
        }
        if self.collapsed {
            return Region::Mini;
        }
        if x < HANDLE_WIDTH {
            return Region::Handle;
        }
        let mut left = HANDLE_WIDTH;
        for (index, width) in self.item_widths.iter().enumerate() {
            if x >= left && x < left + *width {
                return Region::Item(index);
            }
            left += *width + ITEM_GAP;
        }
        Region::Settings
    }

    fn item_rect(&self, index: usize) -> RECT {
        let left = HANDLE_WIDTH
            + self.item_widths[..index].iter().sum::<i32>()
            + ITEM_GAP * index as i32;
        RECT {
            left,
            top: 1,
            right: left + self.item_widths[index],
            bottom: BAR_HEIGHT - 1,
        }
    }

    fn paint(&self) {
        let mut paint: PAINTSTRUCT = unsafe { zeroed() };
        let hdc = unsafe { BeginPaint(self.hwnd, &mut paint) };
        if hdc == 0 {
            return;
        }

        let mut client: RECT = unsafe { zeroed() };
        unsafe { GetClientRect(self.hwnd, &mut client) };
        fill(hdc, &client, BG);
        frame(hdc, &client, BORDER, 1);

        if self.collapsed {
            let rect = RECT { left: 1, top: 1, right: client.right - 1, bottom: client.bottom - 1 };
            fill(hdc, &rect, if self.hover == Region::Mini { PANEL_HOVER } else { PANEL });
            draw_dot(hdc, 13, BAR_HEIGHT / 2, self.aggregate_colour());
            draw_text(hdc, self.font, "PSX", RECT { left: 24, top: 0, right: client.right - 7, bottom: BAR_HEIGHT }, TEXT, DT_LEFT | DT_SINGLELINE | DT_VCENTER);
        } else {
            draw_hamburger(hdc, self.hover == Region::Handle);
            for (index, item) in self.config.items.iter().enumerate() {
                let rect = self.item_rect(index);
                fill(hdc, &rect, if self.hover == Region::Item(index) { PANEL_HOVER } else { PANEL });
                draw_dot(hdc, rect.left + 14, BAR_HEIGHT / 2, self.statuses[index].colour());
                draw_text(
                    hdc,
                    self.font,
                    &item.label,
                    RECT { left: rect.left + 27, top: 0, right: rect.right - 8, bottom: BAR_HEIGHT },
                    TEXT,
                    DT_LEFT | DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS,
                );
            }
            let settings_left = client.right - SETTINGS_WIDTH - 1;
            let rect = RECT { left: settings_left, top: 1, right: client.right - 1, bottom: BAR_HEIGHT - 1 };
            fill(hdc, &rect, BG);
            draw_text(hdc, self.font, "⋯", rect, if self.hover == Region::Settings { TEXT } else { MUTED }, DT_CENTER | DT_SINGLELINE | DT_VCENTER);
        }

        unsafe { EndPaint(self.hwnd, &paint) };
    }

    fn aggregate_colour(&self) -> u32 {
        if self.statuses.iter().any(|state| *state == ButtonState::Error) {
            RED
        } else {
            let active: Vec<_> = self.statuses.iter().filter(|state| **state != ButtonState::Off).collect();
            if active.is_empty() {
                OFF_DOT
            } else if active.iter().any(|state| **state == ButtonState::Partial) {
                ORANGE
            } else {
                GREEN
            }
        }
    }

    fn invalidate(&self) {
        unsafe { InvalidateRect(self.hwnd, null(), 0) };
    }

    fn set_hover(&mut self, region: Region) {
        if self.hover != region {
            self.hover = region;
            self.invalidate();
        }
    }

    fn begin_drag(&mut self) {
        let mut cursor: POINT = unsafe { zeroed() };
        let mut rect: RECT = unsafe { zeroed() };
        unsafe {
            GetCursorPos(&mut cursor);
            GetWindowRect(self.hwnd, &mut rect);
            SetCapture(self.hwnd);
        }
        self.dragging = true;
        self.drag_moved = false;
        self.drag_start = cursor;
        self.drag_offset = POINT { x: cursor.x - rect.left, y: cursor.y - rect.top };
    }

    fn update_drag(&mut self) {
        if !self.dragging {
            return;
        }
        let mut cursor: POINT = unsafe { zeroed() };
        unsafe { GetCursorPos(&mut cursor) };
        if (cursor.x - self.drag_start.x).abs() > 2 || (cursor.y - self.drag_start.y).abs() > 2 {
            self.drag_moved = true;
        }
        let requested_x = cursor.x - self.drag_offset.x;
        let requested_y = cursor.y - self.drag_offset.y;
        let (x, y) = self.clamp_position(requested_x, requested_y, self.window_width(), BAR_HEIGHT);
        unsafe {
            SetWindowPos(
                self.hwnd,
                0,
                x,
                y,
                0,
                0,
                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
            );
        }
    }

    fn end_drag(&mut self) {
        if !self.dragging {
            return;
        }
        self.dragging = false;
        unsafe { ReleaseCapture() };
        if !self.drag_moved {
            self.collapsed = !self.collapsed;
            let mut rect: RECT = unsafe { zeroed() };
            unsafe { GetWindowRect(self.hwnd, &mut rect) };
            let (x, y) = self.clamp_position(rect.left, rect.top, self.window_width(), BAR_HEIGHT);
            unsafe {
                SetWindowPos(
                    self.hwnd,
                    0,
                    x,
                    y,
                    self.window_width(),
                    BAR_HEIGHT,
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
            }
            self.invalidate();
        }
    }

    fn clamp_position(&self, x: i32, y: i32, width: i32, height: i32) -> (i32, i32) {
        let point = POINT { x: x + width / 2, y: y + height / 2 };
        let monitor = unsafe { MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST) };
        let mut info: MONITORINFO = unsafe { zeroed() };
        info.cbSize = size_of::<MONITORINFO>() as u32;
        if monitor != 0 && unsafe { GetMonitorInfoW(monitor, &mut info) } != 0 {
            let work = info.rcWork;
            (
                x.clamp(work.left, (work.right - width).max(work.left)),
                y.clamp(work.top, (work.bottom - height).max(work.top)),
            )
        } else {
            (x.max(0), y.max(0))
        }
    }

    fn launch(&mut self, index: usize) {
        if index >= self.config.items.len() || self.action_busy[index] {
            return;
        }
        self.action_busy[index] = true;
        self.statuses[index] = ButtonState::Running;
        self.launch_locked_until[index] = Some(Instant::now() + Duration::from_secs(12));
        self.invalidate();

        let item = self.config.items[index].clone();
        let hwnd = self.hwnd;
        thread::spawn(move || {
            post_action_result(hwnd, ActionPayload {
                index,
                kind: ActionKind::Launch,
                result: launch::launch_item(&item),
            });
        });
    }

    fn quit_item(&mut self, index: usize) {
        if index >= self.config.items.len() || self.action_busy[index] {
            return;
        }
        self.action_busy[index] = true;
        let item = self.config.items[index].clone();
        let hwnd = self.hwnd;
        thread::spawn(move || {
            post_action_result(hwnd, ActionPayload {
                index,
                kind: ActionKind::Quit,
                result: launch::quit_item(&item),
            });
        });
    }

    fn begin_menu(&mut self, menu: HMENU, anchor: RECT) -> HBRUSH {
        let brush = unsafe { CreateSolidBrush(BG) };
        let mut info: MENUINFO = unsafe { zeroed() };
        info.cbSize = size_of::<MENUINFO>() as u32;
        info.fMask = MIM_BACKGROUND;
        info.hbrBack = brush;
        unsafe { SetMenuInfo(menu, &info); SetTimer(self.hwnd, TIMER_MENU, 50, None); }
        self.menu_open = true;
        self.active_menu = menu;
        self.menu_anchor = anchor;
        self.menu_left_at = None;
        brush
    }

    fn end_menu(&mut self) {
        unsafe { KillTimer(self.hwnd, TIMER_MENU); }
        self.menu_open = false;
        self.active_menu = 0;
        self.menu_left_at = None;
    }

    fn check_menu_pointer(&mut self) {
        if !self.menu_open { return; }
        let mut point: POINT = unsafe { zeroed() };
        unsafe { GetCursorPos(&mut point); }
        let mut inside = contains_point(&self.menu_anchor, point);
        let count = unsafe { GetMenuItemCount(self.active_menu) };
        for index in 0..count.max(0) {
            let mut rect: RECT = unsafe { zeroed() };
            if unsafe { GetMenuItemRect(0, self.active_menu, index as u32, &mut rect) } != 0 {
                // Include the small native popup border between menu items.
                rect.left -= 4; rect.top -= 4; rect.right += 4; rect.bottom += 4;
                inside |= contains_point(&rect, point);
            }
        }
        if inside {
            self.menu_left_at = None;
        } else if self.menu_left_at.get_or_insert_with(Instant::now).elapsed()
            >= Duration::from_millis(250) {
            unsafe { EndMenu(); }
        }
    }

    fn show_item_menu(&mut self, index: usize) {
        let menu = unsafe { CreatePopupMenu() };
        if menu == 0 {
            return;
        }
        let label = wide("Quit apps");
        unsafe { AppendMenuW(menu, MF_OWNERDRAW, (CMD_QUIT_ITEM_BASE + index as u32) as usize, label.as_ptr()) };
        let mut window_rect: RECT = unsafe { zeroed() };
        unsafe { GetWindowRect(self.hwnd, &mut window_rect) };
        let item_rect = self.item_rect(index);
        let brush = self.begin_menu(menu, RECT {
            left: window_rect.left + item_rect.left,
            top: window_rect.top,
            right: window_rect.left + item_rect.right,
            bottom: window_rect.bottom,
        });
        let command = unsafe {
            TrackPopupMenu(
                menu,
                TPM_LEFTALIGN | TPM_TOPALIGN | TPM_RETURNCMD | TPM_RIGHTBUTTON,
                window_rect.left + item_rect.left,
                window_rect.bottom,
                0,
                self.hwnd,
                null(),
            ) as u32
        };
        self.end_menu();
        unsafe { DestroyMenu(menu); DeleteObject(brush); };
        if command >= CMD_QUIT_ITEM_BASE && command < CMD_QUIT_ITEM_BASE + self.config.items.len() as u32 {
            self.quit_item((command - CMD_QUIT_ITEM_BASE) as usize);
        }
    }

    fn show_settings_menu(&mut self) {
        let menu = unsafe { CreatePopupMenu() };
        if menu == 0 {
            return;
        }
        let edit = wide("Edit INI");
        let about = wide(&format!("About ({APP_VERSION})"));
        let quit = wide("Quit");
        unsafe {
            AppendMenuW(menu, MF_OWNERDRAW, CMD_EDIT_INI as usize, edit.as_ptr());
            AppendMenuW(menu, MF_OWNERDRAW, CMD_ABOUT as usize, about.as_ptr());
            AppendMenuW(menu, MF_SEPARATOR, 0, null());
            AppendMenuW(menu, MF_OWNERDRAW, CMD_QUIT as usize, quit.as_ptr());
        }
        let mut rect: RECT = unsafe { zeroed() };
        unsafe { GetWindowRect(self.hwnd, &mut rect) };
        let brush = self.begin_menu(menu, RECT {
            left: rect.right - SETTINGS_WIDTH, top: rect.top,
            right: rect.right, bottom: rect.bottom,
        });
        let command = unsafe {
            TrackPopupMenu(
                menu,
                TPM_RIGHTALIGN | TPM_TOPALIGN | TPM_RETURNCMD | TPM_RIGHTBUTTON,
                rect.right,
                rect.bottom,
                0,
                self.hwnd,
                null(),
            ) as u32
        };
        self.end_menu();
        unsafe { DestroyMenu(menu); DeleteObject(brush); };
        match command {
            CMD_EDIT_INI => {
                if let Err(error) = launch::shell_execute(&self.config.path, self.config.path.parent(), false) {
                    message(self.hwnd, &error, MB_OK | MB_ICONERROR);
                }
            }
            CMD_ABOUT => message(
                self.hwnd,
                &format!("PSX Launcher Rust\nVersion {APP_VERSION}\n\nExperimental native Windows port"),
                MB_OK | MB_ICONINFORMATION,
            ),
            CMD_QUIT => unsafe {
                PostMessageW(self.hwnd, WM_CLOSE, 0, 0);
            },
            _ => {}
        }
    }

    fn request_status(&mut self) {
        if self.status_busy.swap(true, Ordering::AcqRel) {
            return;
        }
        let items = self.config.items.clone();
        let hwnd = self.hwnd;
        let busy = Arc::clone(&self.status_busy);
        thread::spawn(move || {
            let processes = inventory();
            let states = items
                .iter()
                .map(|item| status_for_item(item, &processes))
                .collect();
            busy.store(false, Ordering::Release);
            let payload = Box::new(StatusPayload(states));
            let raw = Box::into_raw(payload);
            if unsafe { PostMessageW(hwnd, WM_STATUS_READY, 0, raw as isize) } == 0 {
                unsafe { drop(Box::from_raw(raw)) };
            }
        });
    }

    fn apply_status(&mut self, payload: Box<StatusPayload>) {
        if payload.0.len() == self.statuses.len() {
            let now = Instant::now();
            self.statuses = payload
                .0
                .into_iter()
                .enumerate()
                .map(|(index, state)| {
                    if state == ButtonState::Off
                        && self.launch_locked_until[index].is_some_and(|until| until > now)
                    {
                        ButtonState::Running
                    } else {
                        if state != ButtonState::Off {
                            self.launch_locked_until[index] = None;
                        }
                        state
                    }
                })
                .collect();
            self.invalidate();
        }
    }

    fn apply_action(&mut self, payload: Box<ActionPayload>) {
        let ActionPayload { index, kind, result } = *payload;
        if index >= self.config.items.len() {
            return;
        }
        self.action_busy[index] = false;
        match result {
            Ok(()) => match kind {
                ActionKind::Launch => {
                    self.statuses[index] = ButtonState::Running;
                }
                ActionKind::Quit => {
                    self.statuses[index] = ButtonState::Off;
                    self.launch_locked_until[index] = None;
                }
            },
            Err(error) => {
                self.statuses[index] = ButtonState::Error;
                self.launch_locked_until[index] = None;
                message(self.hwnd, &error, MB_OK | MB_ICONERROR);
            }
        }
        self.invalidate();
    }

    fn save_position(&self) {
        let mut rect: RECT = unsafe { zeroed() };
        if unsafe { GetWindowRect(self.hwnd, &mut rect) } != 0 {
            let _ = config::save_position(&self.config.path, rect.left, rect.top);
        }
    }
}

fn status_for_item(item: &LauncherItem, processes: &[processes::WindowsProcess]) -> ButtonState {
    if item.targets.is_empty() {
        return ButtonState::Off;
    }
    let states: Vec<bool> = item
        .targets
        .iter()
        .map(|target| target_is_running(target, processes))
        .collect();
    if states.iter().all(|running| *running) {
        ButtonState::Running
    } else if states.iter().any(|running| *running) {
        ButtonState::Partial
    } else {
        ButtonState::Off
    }
}

fn post_action_result(hwnd: HWND, payload: ActionPayload) {
    let raw = Box::into_raw(Box::new(payload));
    if unsafe { PostMessageW(hwnd, WM_ACTION_READY, 0, raw as isize) } == 0 {
        unsafe { drop(Box::from_raw(raw)) };
    }
}

unsafe extern "system" fn window_proc(hwnd: HWND, message_id: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe {
    if message_id == WM_NCCREATE {
        let create = &*(lparam as *const CREATESTRUCTW);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, create.lpCreateParams as isize);
        let state = &mut *(create.lpCreateParams as *mut AppState);
        state.hwnd = hwnd;
    }

    let pointer = GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut AppState;
    if pointer.is_null() {
        return DefWindowProcW(hwnd, message_id, wparam, lparam);
    }
    let state = &mut *pointer;

    match message_id {
        WM_MEASUREITEM => {
            let measure = &mut *(lparam as *mut MEASUREITEMSTRUCT);
            if measure.CtlType == ODT_MENU {
                measure.itemHeight = 28;
                measure.itemWidth = if measure.itemID >= CMD_QUIT_ITEM_BASE && measure.itemID < CMD_EDIT_INI { 112 } else { 200 };
                1
            } else { DefWindowProcW(hwnd, message_id, wparam, lparam) }
        }
        WM_DRAWITEM => {
            let draw = &*(lparam as *const DRAWITEMSTRUCT);
            if draw.CtlType == ODT_MENU {
                fill(draw.hDC, &draw.rcItem, if draw.itemState & ODS_SELECTED != 0 { PANEL_HOVER } else { BG });
                let label = match draw.itemID {
                    CMD_EDIT_INI => "Edit INI".to_string(),
                    CMD_ABOUT => format!("About ({APP_VERSION})"),
                    CMD_QUIT => "Quit".to_string(),
                    _ => "Quit apps".to_string(),
                };
                let mut rect = draw.rcItem; rect.left += 8;
                draw_text(draw.hDC, state.font, &label, rect, TEXT, DT_LEFT | DT_SINGLELINE | DT_VCENTER);
                1
            } else { DefWindowProcW(hwnd, message_id, wparam, lparam) }
        }
        WM_ACTIVATEAPP if wparam == 0 => {
            if state.menu_open { EndMenu(); }
            0
        }
        WM_CREATE => 0,
        WM_PAINT => {
            state.paint();
            0
        }
        WM_ERASEBKGND => 1,
        WM_MOUSEMOVE => {
            if state.dragging {
                state.update_drag();
            } else {
                state.set_hover(state.region_at(get_x_lparam(lparam), get_y_lparam(lparam)));
                if !state.tracking_mouse {
                    let mut tracking = TRACKMOUSEEVENT {
                        cbSize: size_of::<TRACKMOUSEEVENT>() as u32,
                        dwFlags: TME_LEAVE,
                        hwndTrack: hwnd,
                        dwHoverTime: 0,
                    };
                    TrackMouseEvent(&mut tracking);
                    state.tracking_mouse = true;
                }
            }
            0
        }
        WM_MOUSE_LEAVE => {
            state.tracking_mouse = false;
            state.set_hover(Region::None);
            0
        }
        WM_LBUTTONDOWN => {
            let region = state.region_at(get_x_lparam(lparam), get_y_lparam(lparam));
            match region {
                Region::Handle | Region::Mini => state.begin_drag(),
                Region::Item(index) => state.launch(index),
                Region::Settings => state.show_settings_menu(),
                Region::None => {}
            }
            0
        }
        WM_LBUTTONUP => {
            state.end_drag();
            0
        }
        WM_RBUTTONUP => {
            if let Region::Item(index) = state.region_at(get_x_lparam(lparam), get_y_lparam(lparam)) {
                state.show_item_menu(index);
            }
            0
        }
        WM_TIMER => {
            match wparam {
                TIMER_MENU => state.check_menu_pointer(),
                TIMER_STATUS => {
                    if state.first_status_timer {
                        KillTimer(hwnd, TIMER_STATUS);
                        SetTimer(hwnd, TIMER_STATUS, 5000, None);
                        state.first_status_timer = false;
                    }
                    state.request_status();
                }
                TIMER_TOPMOST if state.config.always_on_top && !state.menu_open => {
                    SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE);
                }
                _ => {}
            }
            0
        }
        WM_STATUS_READY => {
            if lparam != 0 {
                state.apply_status(Box::from_raw(lparam as *mut StatusPayload));
            }
            0
        }
        WM_ACTION_READY => {
            if lparam != 0 {
                state.apply_action(Box::from_raw(lparam as *mut ActionPayload));
            }
            0
        }
        WM_DPICHANGED => {
            let suggested = &*(lparam as *const RECT);
            SetWindowPos(
                hwnd,
                0,
                suggested.left,
                suggested.top,
                suggested.right - suggested.left,
                suggested.bottom - suggested.top,
                SWP_NOZORDER | SWP_NOACTIVATE,
            );
            0
        }
        WM_CLOSE => {
            state.save_position();
            DestroyWindow(hwnd);
            0
        }
        WM_DESTROY => {
            KillTimer(hwnd, TIMER_STATUS);
            KillTimer(hwnd, TIMER_TOPMOST);
            KillTimer(hwnd, TIMER_MENU);
            if state.font != 0 {
                DeleteObject(state.font);
                state.font = 0;
            }
            PostQuitMessage(0);
            0
        }
        _ => DefWindowProcW(hwnd, message_id, wparam, lparam),
    }
    }
}

fn main() {
    unsafe { SetProcessDPIAware() };

    let mutex_name = wide(MUTEX_NAME);
    let mutex = unsafe { CreateMutexW(null(), 1, mutex_name.as_ptr()) };
    if mutex == 0 {
        message(0, "Cannot create the single-instance mutex.", MB_OK | MB_ICONERROR);
        return;
    }
    if unsafe { GetLastError() } == ERROR_ALREADY_EXISTS {
        message(0, "PSX Launcher Rust is already running.", MB_OK | MB_ICONINFORMATION);
        unsafe { CloseHandle(mutex) };
        return;
    }

    let config = match config::load() {
        Ok(config) => config,
        Err(error) => {
            message(0, &error, MB_OK | MB_ICONERROR);
            unsafe {
                ReleaseMutex(mutex);
                CloseHandle(mutex);
            }
            return;
        }
    };

    let instance = unsafe { GetModuleHandleW(null()) };
    let class_name = wide(CLASS_NAME);
    let icon = unsafe { LoadIconW(instance, 1 as *const u16) };
    let cursor = unsafe { LoadCursorW(0, IDC_ARROW) };
    let class = WNDCLASSEXW {
        cbSize: size_of::<WNDCLASSEXW>() as u32,
        style: CS_HREDRAW | CS_VREDRAW,
        lpfnWndProc: Some(window_proc),
        cbClsExtra: 0,
        cbWndExtra: 0,
        hInstance: instance,
        hIcon: icon,
        hCursor: cursor,
        hbrBackground: 0,
        lpszMenuName: null(),
        lpszClassName: class_name.as_ptr(),
        hIconSm: icon,
    };
    if unsafe { RegisterClassExW(&class) } == 0 {
        message(0, "Cannot register the launcher window class.", MB_OK | MB_ICONERROR);
        unsafe {
            ReleaseMutex(mutex);
            CloseHandle(mutex);
        }
        return;
    }

    let mut state = Box::new(AppState::new(config));
    let title = wide(&format!("{APP_NAME} {APP_VERSION}"));
    let hwnd = unsafe {
        CreateWindowExW(
            WS_EX_TOOLWINDOW | if state.config.always_on_top { WS_EX_TOPMOST } else { 0 },
            class_name.as_ptr(),
            title.as_ptr(),
            WS_POPUP,
            state.config.x,
            state.config.y,
            1,
            1,
            0,
            0,
            instance,
            state.as_mut() as *mut AppState as *mut _,
        )
    };
    if hwnd == 0 {
        message(0, "Cannot create the launcher window.", MB_OK | MB_ICONERROR);
    } else {
        state.initialize_window();
        let mut message_data: MSG = unsafe { zeroed() };
        while unsafe { GetMessageW(&mut message_data, 0, 0, 0) } > 0 {
            unsafe {
                TranslateMessage(&message_data);
                DispatchMessageW(&message_data);
            }
        }
    }

    unsafe {
        ReleaseMutex(mutex);
        CloseHandle(mutex);
    }
}

fn contains_point(rect: &RECT, point: POINT) -> bool {
    point.x >= rect.left && point.x < rect.right
        && point.y >= rect.top && point.y < rect.bottom
}

fn fill(hdc: HDC, rect: &RECT, colour: u32) {
    let brush = unsafe { CreateSolidBrush(colour) };
    unsafe {
        FillRect(hdc, rect, brush);
        DeleteObject(brush);
    }
}

fn frame(hdc: HDC, rect: &RECT, colour: u32, thickness: i32) {
    fill(hdc, &RECT { left: rect.left, top: rect.top, right: rect.right, bottom: rect.top + thickness }, colour);
    fill(hdc, &RECT { left: rect.left, top: rect.bottom - thickness, right: rect.right, bottom: rect.bottom }, colour);
    fill(hdc, &RECT { left: rect.left, top: rect.top, right: rect.left + thickness, bottom: rect.bottom }, colour);
    fill(hdc, &RECT { left: rect.right - thickness, top: rect.top, right: rect.right, bottom: rect.bottom }, colour);
}

fn draw_dot(hdc: HDC, x: i32, y: i32, colour: u32) {
    let brush = unsafe { CreateSolidBrush(colour) };
    let old_brush = unsafe { SelectObject(hdc, brush) };
    let old_pen = unsafe { SelectObject(hdc, GetStockObject(NULL_PEN)) };
    unsafe {
        Ellipse(hdc, x - 4, y - 4, x + 5, y + 5);
        SelectObject(hdc, old_pen);
        SelectObject(hdc, old_brush);
        DeleteObject(brush);
    }
}

fn draw_hamburger(hdc: HDC, hover: bool) {
    let pen = unsafe { CreatePen(PS_SOLID, 2, if hover { TEXT } else { MUTED }) };
    let old = unsafe { SelectObject(hdc, pen) };
    for y in [9, 17, 25] {
        unsafe {
            MoveToEx(hdc, 8, y, null_mut());
            LineTo(hdc, 26, y);
        }
    }
    unsafe {
        SelectObject(hdc, old);
        DeleteObject(pen);
    }
}

fn draw_text(hdc: HDC, font: HFONT, text: &str, mut rect: RECT, colour: u32, format: u32) {
    let mut value = wide(text);
    unsafe {
        let old = SelectObject(hdc, font);
        SetBkMode(hdc, TRANSPARENT as i32);
        SetTextColor(hdc, colour);
        DrawTextW(hdc, value.as_mut_ptr(), value.len().saturating_sub(1) as i32, &mut rect, format);
        SelectObject(hdc, old);
    }
}

fn message(owner: HWND, text: &str, flags: u32) {
    let title = wide(APP_NAME);
    let text = wide(text);
    unsafe { MessageBoxW(owner, text.as_ptr(), title.as_ptr(), flags) };
}
