use image::imageops::FilterType;
use tray_icon::{Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OverallStatus { Off, Partial, Running, Error }

// Empty/disabled sections do not count towards the total.
pub fn overall_status(states: impl IntoIterator<Item = (usize, bool, bool, bool)>) -> OverallStatus {
    let mut any = false;
    let mut all = true;
    let mut configured = false;
    for (targets, running, complete, error) in states {
        if targets == 0 { continue; }
        configured = true;
        if error { return OverallStatus::Error; }
        any |= running;
        all &= complete;
    }
    if !configured || !any { OverallStatus::Off }
    else if all { OverallStatus::Running }
    else { OverallStatus::Partial }
}

pub struct StatusIcon {
    tray: TrayIcon,
    icons: [Icon; 4],
    status: OverallStatus,
}

impl StatusIcon {
    pub fn new(on_click: impl Fn() + Send + Sync + 'static) -> Result<Self, String> {
        // Rasterize the actual repository ICNS at build time, retaining its colors.
        let base = image::load_from_memory(include_bytes!(concat!(env!("OUT_DIR"), "/status-icon.png")))
            .map_err(|e| e.to_string())?
            .resize_exact(64, 64, FilterType::Lanczos3)
            .to_rgba8();
        let make_icon = |color: [u8; 3]| -> Result<Icon, String> {
            let mut pixels = base.clone();
            // High-resolution lower-right badge with a dark outline.
            for y in 0..64 {
                for x in 0..64 {
                    let distance = ((x as f32 + 0.5 - 51.0).powi(2)
                        + (y as f32 + 0.5 - 51.0).powi(2)).sqrt();
                    if distance < 12.0 {
                        let rgb = if distance < 9.5 { color } else { [23, 25, 28] };
                        let alpha = (12.0 - distance).clamp(0.0, 1.0);
                        let pixel = pixels.get_pixel_mut(x, y);
                        for channel in 0..3 {
                            pixel[channel] = (rgb[channel] as f32 * alpha
                                + pixel[channel] as f32 * (1.0 - alpha)).round() as u8;
                        }
                        pixel[3] = (255.0 * alpha + pixel[3] as f32 * (1.0 - alpha)).round() as u8;
                    }
                }
            }
            Icon::from_rgba(pixels.into_raw(), 64, 64).map_err(|e| e.to_string())
        };
        let icons = [make_icon([98,104,112])?, make_icon([231,168,75])?,
            make_icon([84,200,120])?, make_icon([223,100,100])?];
        let tray = TrayIconBuilder::new()
            .with_tooltip("PSX Launcher — no configured apps running; click to show")
            .with_icon(icons[0].clone())
            .with_icon_as_template(false)
            .with_menu_on_left_click(false)
            .build().map_err(|e| e.to_string())?;
        TrayIconEvent::set_event_handler(Some(move |event| {
            if matches!(event, TrayIconEvent::Click {
                button: MouseButton::Left, button_state: MouseButtonState::Up, ..
            }) { on_click(); }
        }));
        Ok(Self { tray, icons, status: OverallStatus::Off })
    }

    pub fn update(&mut self, status: OverallStatus) {
        if self.status == status { return; }
        let (index, label) = match status {
            OverallStatus::Off => (0, "no configured apps running"),
            OverallStatus::Partial => (1, "some configured apps are not running"),
            OverallStatus::Running => (2, "all configured apps running"),
            OverallStatus::Error => (3, "an app action failed"),
        };
        if self.tray.set_icon(Some(self.icons[index].clone())).is_ok() {
            self.status = status;
        }
        let _ = self.tray.set_tooltip(Some(format!("PSX Launcher — {label}; click to show")));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test] fn all_running_is_green() {
        assert_eq!(overall_status([(2,true,true,false),(1,true,true,false)]), OverallStatus::Running);
    }
    #[test] fn one_missing_is_orange() {
        assert_eq!(overall_status([(1,true,true,false),(1,false,false,false)]), OverallStatus::Partial);
    }
    #[test] fn incomplete_pair_is_orange() {
        assert_eq!(overall_status([(2,true,false,false)]), OverallStatus::Partial);
    }
    #[test] fn errors_are_red() {
        assert_eq!(overall_status([(1,false,false,true)]), OverallStatus::Error);
    }
    #[test] fn none_running_is_gray() {
        assert_eq!(overall_status([(2,false,false,false)]), OverallStatus::Off);
    }
    #[test] fn empty_sections_are_ignored() {
        assert_eq!(overall_status([(0,false,false,false),(1,true,true,false)]), OverallStatus::Running);
    }
}
