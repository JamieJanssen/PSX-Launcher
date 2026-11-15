#[cfg(not(target_os = "macos"))]
compile_error!("PSX Launcher Rust for macOS can only be built on macOS.");

mod config;
mod processes;

use config::AppConfig;
use eframe::egui::{self, Color32, FontId, Pos2, Sense, Vec2, ViewportCommand, ViewportId};
use fs2::FileExt;
use std::fs::File;
use std::process::{Command, Stdio};
use std::sync::mpsc::{self, Receiver, Sender};
use std::thread;
use std::time::{Duration, Instant};
use winit::platform::macos::{ActivationPolicy, EventLoopBuilderExtMacOS};

const VERSION:&str="2.0.0-alpha.1";
const HEIGHT:f32=28.0;
const HANDLE:f32=28.0;
const SETTINGS:f32=28.0;
const MINI:f32=54.0;
const BG:Color32=Color32::from_rgb(0x17,0x19,0x1c);
const PANEL:Color32=Color32::from_rgb(0x22,0x25,0x2a);
const HOVER:Color32=Color32::from_rgb(0x2d,0x31,0x37);
const TEXT:Color32=Color32::from_rgb(0xf2,0xf3,0xf5);
const MUTED:Color32=Color32::from_rgb(0x96,0x9c,0xa5);
const OFF:Color32=Color32::from_rgb(0x62,0x68,0x70);
const GREEN:Color32=Color32::from_rgb(0x54,0xc8,0x78);
const ORANGE:Color32=Color32::from_rgb(0xe7,0xa8,0x4b);
const RED:Color32=Color32::from_rgb(0xdf,0x64,0x64);

#[derive(Clone,Copy,PartialEq)] enum State{Off,Partial,Running,Error}
impl State{fn color(self)->Color32{match self{Self::Off=>OFF,Self::Partial=>ORANGE,Self::Running=>GREEN,Self::Error=>RED}}}
enum Event{Status(Vec<State>),Action(usize,bool,Result<(),String>)}
#[derive(Clone,Copy)] enum Menu{Item(usize,f32),Settings(f32),About(f32)}

struct LauncherApp{
    config:AppConfig,status:Vec<State>,locked:Vec<Option<Instant>>,busy:Vec<bool>,widths:Vec<f32>,expanded:f32,collapsed:bool,
    menu_left_at:Option<Instant>,menu_app_was_active:bool,menu_focus_requested:bool,menu_had_focus:bool,menu:Option<Menu>,tx:Sender<Event>,rx:Receiver<Event>,status_busy:bool,next_status:Instant,last_pos:Pos2,
}

impl LauncherApp{
    fn new(config:AppConfig,tx:Sender<Event>,rx:Receiver<Event>)->Self{
        let widths:Vec<_>=config.items.iter().map(|item|(38.0+item.label.chars().count() as f32*6.5).max(58.0)).collect();
        let expanded=HANDLE+SETTINGS+widths.iter().sum::<f32>()+2.0*widths.len().saturating_sub(1) as f32;
        let count=config.items.len(); let x=config.x as f32; let y=config.y as f32;
        Self{config,status:vec![State::Off;count],locked:vec![None;count],busy:vec![false;count],widths,expanded,collapsed:false,menu_left_at:None,menu_app_was_active:false,menu_focus_requested:false,menu_had_focus:false,menu:None,tx,rx,status_busy:false,next_status:Instant::now()+Duration::from_secs(2),last_pos:Pos2::new(x,y)}
    }
    fn process_events(&mut self){
        while let Ok(event)=self.rx.try_recv(){match event{
            Event::Status(states)=>{self.status_busy=false;let now=Instant::now();for(i,state)in states.into_iter().enumerate(){self.status[i]=if state==State::Off&&self.locked[i].is_some_and(|t|t>now){State::Running}else{if state!=State::Off{self.locked[i]=None;}state};}}
            Event::Action(index,quit,result)=>{self.busy[index]=false;match result{Ok(())=>{if quit{self.status[index]=State::Off;self.locked[index]=None;}else{self.status[index]=State::Running;}},Err(error)=>{self.status[index]=State::Error;self.locked[index]=None;show_error(&error);}}}
        }}
    }
    fn request_status(&mut self){if self.status_busy{return;}self.status_busy=true;let items=self.config.items.clone();let tx=self.tx.clone();thread::spawn(move||{let snapshot=processes::snapshot();let states=items.iter().map(|item|{let(any,all)=processes::item_state(item,&snapshot);if all{State::Running}else if any{State::Partial}else{State::Off}}).collect();let _=tx.send(Event::Status(states));});}
    fn action(&mut self,index:usize,quit:bool){if self.busy[index]{return;}self.busy[index]=true;if !quit{self.status[index]=State::Running;self.locked[index]=Some(Instant::now()+Duration::from_secs(12));}let item=self.config.items[index].clone();let tx=self.tx.clone();thread::spawn(move||{let result=if quit{processes::quit_item(&item)}else{processes::launch_item(&item)};let _=tx.send(Event::Action(index,quit,result));});}
    fn draw_item(&mut self,ui:&mut egui::Ui,index:usize){
        let(rect,response)=ui.allocate_exact_size(Vec2::new(self.widths[index],HEIGHT-2.0),Sense::click());let fill=if response.hovered(){HOVER}else{PANEL};ui.painter().rect_filled(rect,0.0,fill);
        ui.painter().circle_filled(Pos2::new(rect.left()+13.0,rect.center().y),4.0,self.status[index].color());
        ui.painter().text(Pos2::new(rect.left()+25.0,rect.center().y-1.0),egui::Align2::LEFT_CENTER,&self.config.items[index].label,FontId::proportional(11.0),TEXT);
        if response.clicked(){self.action(index,false);} if response.secondary_clicked(){self.menu=Some(Menu::Item(index,rect.left()));}
    }
    fn show_menu(&mut self,ctx:&egui::Context){
        let Some(menu)=self.menu else{return;};let origin=ctx.input(|i|i.viewport().outer_rect.map(|r|r.min).unwrap_or(self.last_pos));
        let(x,size)=match menu{Menu::Item(_,x)=>(x,Vec2::new(112.0,32.0)),Menu::Settings(x)=>(x-188.0+SETTINGS,Vec2::new(188.0,94.0)),Menu::About(x)=>(x-70.0,Vec2::new(210.0,118.0))};
        let button_rect = match menu {
            Menu::Item(index, left) => Some(egui::Rect::from_min_size(
                Pos2::new(left, 0.0), Vec2::new(self.widths[index], HEIGHT),
            )),
            Menu::Settings(left) => Some(egui::Rect::from_min_size(
                Pos2::new(left, 0.0), Vec2::new(SETTINGS, HEIGHT),
            )),
            Menu::About(_) => None,
        };
        let over_button = ctx.input(|i| {
            i.pointer.hover_pos().is_some_and(|pos| {
                button_rect.is_some_and(|rect| rect.contains(pos))
            })
        });
        let mut over_menu = false;
        let app_active = mac_app_is_active();
        let lost_app_focus = self.menu_app_was_active && !app_active;
        self.menu_app_was_active |= app_active;
        let mut close=lost_app_focus;let mut action=None;let id=ViewportId::from_hash_of("psx-launcher-popup");
        ctx.show_viewport_immediate(id,egui::ViewportBuilder::default().with_title("PSX Launcher").with_decorations(false).with_always_on_top().with_taskbar(false).with_resizable(false).with_inner_size(size).with_position(origin+Vec2::new(x,HEIGHT)),|popup,_|{
            egui::CentralPanel::default().frame(egui::Frame::NONE.fill(BG).inner_margin(egui::Margin::same(5))).show(popup,|ui|{match menu{
                Menu::Item(index,_)=>{if menu_button(ui, "Quit apps").clicked(){action=Some((index,true));close=true;}},
                Menu::Settings(_)=>{if menu_button(ui, "Edit INI").clicked(){let _=processes::spawn_reaped(Command::new("/usr/bin/open").arg(&self.config.path));close=true;}if menu_button(ui, &format!("About ({VERSION})")).clicked(){self.menu=Some(Menu::About(x));}if menu_button(ui, "Quit").clicked(){close = true;popup.send_viewport_cmd_to(ViewportId::ROOT, ViewportCommand::Close);}},
                Menu::About(_)=>{ui.vertical_centered(|ui|{ui.label(egui::RichText::new("PSX Launcher").color(TEXT).strong());ui.label(egui::RichText::new(format!("Version {VERSION}")).color(MUTED));if menu_button(ui, "Close").clicked(){close=true;}});}
            }
            });
            if !self.menu_focus_requested {
                popup.send_viewport_cmd(ViewportCommand::Focus);
                self.menu_focus_requested = true;
            }
            over_menu = popup.input(|i| {
                i.pointer.hover_pos().is_some_and(|pos| {
                    egui::Rect::from_min_size(Pos2::ZERO, size).contains(pos)
                })
            });
            let focused = popup.input(|i| i.viewport().focused);
            if focused == Some(true) {
                self.menu_had_focus = true;
            }
            if (self.menu_had_focus && focused == Some(false))
                || popup.input(|i| i.key_pressed(egui::Key::Escape))
                || popup.input(|i| i.viewport().close_requested()) {
                close = true;
            }
        });
        if !matches!(menu, Menu::About(_)) {
            if over_button || over_menu {
                self.menu_left_at = None;
            } else {
                let left_at = self.menu_left_at.get_or_insert_with(Instant::now);
                if left_at.elapsed() >= Duration::from_millis(250) {
                    close = true;
                }
                ctx.request_repaint_after(Duration::from_millis(25));
            }
        }
        if let Some((index,quit))=action{self.action(index,quit);}if close{ctx.send_viewport_cmd_to(id,ViewportCommand::Close);self.menu=None;}
    }
}

impl eframe::App for LauncherApp{
    fn update(&mut self,ctx:&egui::Context,_frame:&mut eframe::Frame){
        if self.menu.is_some() && ctx.input(|i| i.pointer.any_pressed() || i.key_pressed(egui::Key::Escape)) {
            ctx.send_viewport_cmd_to(ViewportId::from_hash_of("psx-launcher-popup"), ViewportCommand::Close);
            self.menu = None;
        }
        if self.menu.is_none() {
            self.menu_left_at = None;
            self.menu_app_was_active = false;
            self.menu_focus_requested = false;
            self.menu_had_focus = false;
        }
        self.process_events();if Instant::now()>=self.next_status{self.request_status();self.next_status=Instant::now()+Duration::from_secs(5);}
        if let Some(pos)=ctx.input(|i|i.viewport().outer_rect.map(|r|r.min)){self.last_pos=pos;}
        egui::CentralPanel::default().frame(egui::Frame::NONE.fill(BG).inner_margin(egui::Margin::same(1))).show(ctx,|ui|{
            ui.spacing_mut().item_spacing=Vec2::new(2.0,0.0);ui.horizontal(|ui|{
                if self.collapsed{let(rect,response)=ui.allocate_exact_size(Vec2::new(MINI-2.0,HEIGHT-2.0),Sense::click_and_drag());ui.painter().rect_filled(rect,0.0,if response.hovered(){HOVER}else{PANEL});ui.painter().circle_filled(Pos2::new(rect.left()+12.0,rect.center().y),4.0,self.status.iter().find(|s|**s!=State::Off).copied().unwrap_or(State::Off).color());ui.painter().text(Pos2::new(rect.left()+23.0,rect.center().y-1.0),egui::Align2::LEFT_CENTER,"PSX",FontId::proportional(11.0),TEXT);if response.drag_started(){ctx.send_viewport_cmd(ViewportCommand::StartDrag);}if response.clicked(){self.collapsed=false;ctx.send_viewport_cmd(ViewportCommand::InnerSize(Vec2::new(self.expanded,HEIGHT)));}}
                else{let(rect,response)=ui.allocate_exact_size(Vec2::new(HANDLE-2.0,HEIGHT-2.0),Sense::click_and_drag());for y in[-6.0,0.0,6.0]{ui.painter().line_segment([Pos2::new(rect.left()+6.0,rect.center().y+y),Pos2::new(rect.right()-6.0,rect.center().y+y)],egui::Stroke::new(2.0_f32,if response.hovered(){TEXT}else{MUTED}));}if response.drag_started(){ctx.send_viewport_cmd(ViewportCommand::StartDrag);}if response.clicked(){self.collapsed=true;ctx.send_viewport_cmd(ViewportCommand::InnerSize(Vec2::new(MINI,HEIGHT)));}for index in 0..self.config.items.len(){self.draw_item(ui,index);}let(rect,response)=ui.allocate_exact_size(Vec2::new(SETTINGS-2.0,HEIGHT-2.0),Sense::click());for offset in [-4.0, 0.0, 4.0] { ui.painter().circle_filled(rect.center()+Vec2::new(offset,0.0),1.2,if response.hovered(){TEXT}else{MUTED}); }if response.clicked()||response.secondary_clicked(){self.menu=Some(Menu::Settings(rect.left()));}}
            });
        });self.show_menu(ctx);ctx.request_repaint_after(Duration::from_millis(100));
    }
    fn on_exit(&mut self,_gl:Option<&eframe::glow::Context>){let _=config::save_position(&self.config.path,self.last_pos.x.round() as i32,self.last_pos.y.round() as i32);}
}

// AppKit activation is independent of accessory popup window focus events.
#[link(name = "objc")]
unsafe extern "C" {
    fn objc_getClass(name: *const std::ffi::c_char) -> *mut std::ffi::c_void;
    fn sel_registerName(name: *const std::ffi::c_char) -> *mut std::ffi::c_void;
    fn objc_msgSend();
}

fn mac_app_is_active() -> bool {
    // Called only on eframe's main/UI thread. Both selectors are argument-free.
    unsafe {
        let class = objc_getClass(c"NSApplication".as_ptr());
        if class.is_null() { return false; }
        let shared = sel_registerName(c"sharedApplication".as_ptr());
        let send_object: unsafe extern "C" fn(
            *mut std::ffi::c_void, *mut std::ffi::c_void,
        ) -> *mut std::ffi::c_void = std::mem::transmute(objc_msgSend as *const ());
        let app = send_object(class, shared);
        if app.is_null() { return false; }
        let active = sel_registerName(c"isActive".as_ptr());
        let send_bool: unsafe extern "C" fn(
            *mut std::ffi::c_void, *mut std::ffi::c_void,
        ) -> i8 = std::mem::transmute(objc_msgSend as *const ());
        send_bool(app, active) != 0
    }
}

fn menu_button(ui: &mut egui::Ui, text: &str) -> egui::Response {
    let width = ui.available_width();
    let (rect, response) = ui.allocate_exact_size(Vec2::new(width, 24.0), Sense::click());
    ui.painter().rect_filled(rect, 0.0, if response.hovered() { HOVER } else { BG });
    ui.painter().text(
        rect.left_center() + Vec2::new(6.0, 0.0),
        egui::Align2::LEFT_CENTER,
        text,
        FontId::proportional(11.0),
        TEXT,
    );
    response
}

fn show_error(message:&str){let escaped=message.replace('\\',"\\\\").replace('"',"\\\"").replace('\n',"\\n");let script=format!("display alert \"PSX Launcher\" message \"{escaped}\" as critical");let _=processes::spawn_reaped(Command::new("/usr/bin/osascript").args(["-e",&script]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()));}

fn main()->eframe::Result{
    let config=match config::load(){Ok(v)=>v,Err(e)=>{show_error(&e);return Ok(());}};
    let lock_path=config.path.with_file_name("rust-launcher.lock");
    let lock=match File::create(lock_path){Ok(file)=>file,Err(error)=>{show_error(&format!("Cannot create the single-instance lock:\n\n{error}"));return Ok(());}};
    if lock.try_lock_exclusive().is_err(){show_error("PSX Launcher is already running.");return Ok(());}
    let(tx,rx)=mpsc::channel();let widths:Vec<_>=config.items.iter().map(|i|(38.0+i.label.chars().count() as f32*6.5).max(58.0)).collect();let width=HANDLE+SETTINGS+widths.iter().sum::<f32>()+2.0*widths.len().saturating_sub(1) as f32;
    let position=Pos2::new(config.x as f32,config.y as f32);let always=config.always_on_top;
    let options=eframe::NativeOptions{viewport:egui::ViewportBuilder::default().with_title("PSX Launcher").with_decorations(false).with_taskbar(false).with_resizable(false).with_inner_size([width,HEIGHT]).with_position(position).with_window_level(if always{egui::WindowLevel::AlwaysOnTop}else{egui::WindowLevel::Normal}),event_loop_builder:Some(Box::new(|builder|{builder.with_activation_policy(ActivationPolicy::Accessory).with_default_menu(false);})),..Default::default()};
    let result=eframe::run_native("PSX Launcher",options,Box::new(move|cc|{cc.egui_ctx.set_visuals(egui::Visuals::dark());Ok(Box::new(LauncherApp::new(config,tx,rx)))}));drop(lock);result
}
