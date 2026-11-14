use std::ffi::OsStr;
use std::os::windows::ffi::OsStrExt;

pub fn wide(value: impl AsRef<OsStr>) -> Vec<u16> {
    value.as_ref().encode_wide().chain(Some(0)).collect()
}

pub const fn rgb(red: u8, green: u8, blue: u8) -> u32 {
    red as u32 | ((green as u32) << 8) | ((blue as u32) << 16)
}

pub const fn get_x_lparam(value: isize) -> i32 {
    (value as u32 & 0xffff) as i16 as i32
}

pub const fn get_y_lparam(value: isize) -> i32 {
    ((value as u32 >> 16) & 0xffff) as i16 as i32
}

