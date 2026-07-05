// joycon-remap: make joycond's combined Joy-Cons work correctly in the browser
// (Amazon Luna) WITH rumble.
//
// Problem: Brave/Chromium indexes an unknown gamepad by raw evdev-code order.
// joycond's combined device has an extra capture (BTN_Z) and home (BTN_MODE)
// button and lacks BTN_C, which shifts every button off the standard layout.
//
// This program grabs the combined device and re-emits it as a virtual gamepad
// with three code remaps that restore standard button indices 0-15:
//   BTN_WEST -> BTN_C             (left face into index 2; fixes left/top swap)
//   BTN_Z    -> BTN_TRIGGER_HAPPY5 (capture out of index 4)
//   BTN_MODE -> BTN_TRIGGER_HAPPY6 (home out of the stick-click slots)
//
// Unlike a plain event mapper, it also *services* force-feedback: it forwards
// rumble upload/erase/play from the virtual device back to the combined device,
// which joycond drives to the physical Joy-Con motors. Without this, Chromium's
// rumble upload would stall the gamepad thread (intermittent input freeze).

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <fcntl.h>
#include <unistd.h>
#include <signal.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <linux/input.h>
#include <linux/uinput.h>

#define MAX_FF 64

static int in_fd = -1, ui_fd = -1;

// virtual-device effect id -> combined-device effect id
static int  ff_map[MAX_FF];
static char ff_used[MAX_FF];

// EV_KEY codes the OUTPUT declares, in ascending order so Chromium's raw index
// order equals the standard gamepad layout (0-15) plus SL/SR/capture/home above.
static const int out_keys[] = {
    BTN_SOUTH, BTN_EAST, BTN_C, BTN_NORTH,          // 0 1 2 3  face
    BTN_TL, BTN_TR, BTN_TL2, BTN_TR2,               // 4 5 6 7  L R ZL ZR
    BTN_SELECT, BTN_START, BTN_THUMBL, BTN_THUMBR,  // 8 9 10 11  - + L3 R3
    BTN_DPAD_UP, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT, // 12-15 dpad
    BTN_TRIGGER_HAPPY1, BTN_TRIGGER_HAPPY2,         // SL/SR (single-joycon)
    BTN_TRIGGER_HAPPY3, BTN_TRIGGER_HAPPY4,
    BTN_TRIGGER_HAPPY5, BTN_TRIGGER_HAPPY6,         // capture, home
};
static const int out_abs[] = { ABS_X, ABS_Y, ABS_RX, ABS_RY };

static int remap_key(int code) {
    switch (code) {
        case BTN_WEST: return BTN_C;
        case BTN_Z:    return BTN_TRIGGER_HAPPY5;
        case BTN_MODE: return BTN_TRIGGER_HAPPY6;
        default:       return code;
    }
}

static void cleanup(int sig) {
    (void)sig;
    if (in_fd >= 0) ioctl(in_fd, EVIOCGRAB, 0);
    if (ui_fd >= 0) { ioctl(ui_fd, UI_DEV_DESTROY); close(ui_fd); }
    _exit(0);
}

int main(int argc, char **argv) {
    const char *inpath = argc > 1 ? argv[1] : "/dev/input/joycon-combined";

    in_fd = open(inpath, O_RDWR); // O_RDWR: read events + upload/play FF back
    if (in_fd < 0) { fprintf(stderr, "open %s: %s\n", inpath, strerror(errno)); return 1; }
    if (ioctl(in_fd, EVIOCGRAB, 1) < 0) { perror("EVIOCGRAB"); return 1; }

    ui_fd = open("/dev/uinput", O_RDWR);
    if (ui_fd < 0) { perror("open /dev/uinput"); return 1; }

    ioctl(ui_fd, UI_SET_EVBIT, EV_KEY);
    for (size_t i = 0; i < sizeof(out_keys)/sizeof(*out_keys); i++)
        ioctl(ui_fd, UI_SET_KEYBIT, out_keys[i]);
    ioctl(ui_fd, UI_SET_EVBIT, EV_ABS);
    for (size_t i = 0; i < sizeof(out_abs)/sizeof(*out_abs); i++)
        ioctl(ui_fd, UI_SET_ABSBIT, out_abs[i]);
    ioctl(ui_fd, UI_SET_EVBIT, EV_FF);
    ioctl(ui_fd, UI_SET_FFBIT, FF_RUMBLE);
    ioctl(ui_fd, UI_SET_FFBIT, FF_PERIODIC);
    ioctl(ui_fd, UI_SET_FFBIT, FF_SINE);
    ioctl(ui_fd, UI_SET_FFBIT, FF_SQUARE);
    ioctl(ui_fd, UI_SET_FFBIT, FF_TRIANGLE);
    ioctl(ui_fd, UI_SET_FFBIT, FF_GAIN);

    for (size_t i = 0; i < sizeof(out_abs)/sizeof(*out_abs); i++) {
        struct uinput_abs_setup a; memset(&a, 0, sizeof(a));
        a.code = out_abs[i];
        a.absinfo.minimum = -32767;
        a.absinfo.maximum = 32767;
        a.absinfo.fuzz = 250;
        a.absinfo.flat = 500;
        ioctl(ui_fd, UI_ABS_SETUP, &a);
    }

    struct uinput_setup setup; memset(&setup, 0, sizeof(setup));
    setup.id.bustype = BUS_VIRTUAL;
    setup.id.vendor  = 0x057e;
    setup.id.product = 0x2008;
    setup.id.version = 0;
    setup.ff_effects_max = MAX_FF;                 // enables FF upload callbacks
    strncpy(setup.name, "Joy-Cons Luna Virtual", sizeof(setup.name) - 1);
    if (ioctl(ui_fd, UI_DEV_SETUP, &setup) < 0) { perror("UI_DEV_SETUP"); return 1; }
    if (ioctl(ui_fd, UI_DEV_CREATE) < 0)        { perror("UI_DEV_CREATE"); return 1; }

    signal(SIGINT, cleanup);
    signal(SIGTERM, cleanup);

    struct input_event ev;
    int maxfd = (in_fd > ui_fd ? in_fd : ui_fd) + 1;

    for (;;) {
        fd_set fds; FD_ZERO(&fds); FD_SET(in_fd, &fds); FD_SET(ui_fd, &fds);
        if (select(maxfd, &fds, NULL, NULL, NULL) < 0) {
            if (errno == EINTR) continue;
            perror("select"); break;
        }

        // combined device -> remap -> virtual device
        if (FD_ISSET(in_fd, &fds)) {
            ssize_t n = read(in_fd, &ev, sizeof(ev));
            if (n == (ssize_t)sizeof(ev)) {
                if (ev.type == EV_KEY) ev.code = remap_key(ev.code);
                if (ev.type == EV_KEY || ev.type == EV_ABS || ev.type == EV_SYN)
                    if (write(ui_fd, &ev, sizeof(ev)) < 0) { /* ignore */ }
            }
        }

        // virtual device -> force-feedback -> combined device (joycond -> motors)
        if (FD_ISSET(ui_fd, &fds)) {
            ssize_t n = read(ui_fd, &ev, sizeof(ev));
            if (n != (ssize_t)sizeof(ev)) continue;

            if (ev.type == EV_UINPUT && ev.code == UI_FF_UPLOAD) {
                struct uinput_ff_upload up; memset(&up, 0, sizeof(up));
                up.request_id = ev.value;
                ioctl(ui_fd, UI_BEGIN_FF_UPLOAD, &up);
                struct ff_effect eff = up.effect;
                int oid = up.effect.id;
                if (oid >= 0 && oid < MAX_FF && ff_used[oid])
                    eff.id = ff_map[oid];  // update existing
                else
                    eff.id = -1;           // allocate new on the combined device
                if (ioctl(in_fd, EVIOCSFF, &eff) < 0) {
                    up.retval = errno;
                } else {
                    if (oid >= 0 && oid < MAX_FF) { ff_map[oid] = eff.id; ff_used[oid] = 1; }
                    up.retval = 0;
                }
                ioctl(ui_fd, UI_END_FF_UPLOAD, &up);
            } else if (ev.type == EV_UINPUT && ev.code == UI_FF_ERASE) {
                struct uinput_ff_erase er; memset(&er, 0, sizeof(er));
                er.request_id = ev.value;
                ioctl(ui_fd, UI_BEGIN_FF_ERASE, &er);
                int oid = er.effect_id;
                if (oid >= 0 && oid < MAX_FF && ff_used[oid]) {
                    ioctl(in_fd, EVIOCRMFF, ff_map[oid]);
                    ff_used[oid] = 0;
                }
                er.retval = 0;
                ioctl(ui_fd, UI_END_FF_ERASE, &er);
            } else if (ev.type == EV_FF) {
                // play/stop: translate virtual effect id -> combined effect id
                int oid = ev.code;
                struct input_event play = ev;
                if (oid >= 0 && oid < MAX_FF && ff_used[oid]) play.code = ff_map[oid];
                if (write(in_fd, &play, sizeof(play)) < 0) { /* ignore */ }
            }
        }
    }

    cleanup(0);
    return 0;
}
