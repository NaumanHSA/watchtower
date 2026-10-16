# A tour of the dashboard

The dashboard is the web interface built into the controller. Open the controller's address in a browser (for
the local setup, <http://localhost:7000>). It updates itself live as things change, so you never need to reload
the page.

This page walks through every screen and explains what each number and button means.

## Signing in

<div class="wt-shot" markdown>![The sign-in page](assets/screenshots/sign-in.webp)</div>

There are two ways in:

- **Sign in with SSO** appears when the controller is connected to your company's identity provider. Click it,
  sign in there as usual, and you come straight back to the dashboard. Your session renews itself in the
  background while the tab is open.
- **Access token**: paste a token you were given (for example by an administrator, from the token server) and
  click **Continue**.

By default the dashboard forgets the token when you close the tab. Tick **Remember on this device** to keep it
in this browser until you sign out; don't do that on a shared computer.

After too many failed attempts from one address in a short time (30 in five minutes by default), the page says
*Too many failed sign-in attempts; try again later* for a few minutes. This protects against people guessing
tokens.

## The layout

The **sidebar** on the left has two groups of pages:

- **Monitor**: Overview, Live view and Activity, for watching what is going on.
- **Manage**: Cameras, Workers and, for admins, Audit log, for changing things.

Small badges next to *Cameras* and *Workers* count cameras and online workers. When cameras have problems, the
camera badge turns red and counts those instead.

At the bottom of the sidebar you see whether **live updates** are on (a green dot) and who you are signed in as,
with your role. The arrow button signs you out. On a phone or a narrow window the sidebar folds away behind the
menu button at the top left.

## Overview

<div class="wt-shot" markdown>![The overview page](assets/screenshots/overview.webp)</div>

The Overview is the page to keep open on a wall screen. From top to bottom:

**The banner** has the two most common actions: *Add camera* and *Open live view*.

**Four cards** sum up the fleet:

| Card | What it tells you |
|---|---|
| Cameras running | How many cameras are being analysed right now, out of all cameras. The line below says how many need attention. |
| Workers online | Workers that synced recently, out of all known workers, and how many are draining. |
| Capacity used | Cameras placed on online workers, out of all their slots together. When the bar turns orange (over 85%), add workers or slots before new cameras have nowhere to go. |
| Results delivered | Results the workers sent successfully since they last started, with dropped and failed counts. |

**Needs attention** lists cameras that are *reconnecting* or have *failed*, with the reason. A *Retry* button
appears for failed cameras. When everything is fine, the list says so.

**Fleet** shows each worker with how many of its slots are in use and when it last checked in.

**Recent activity** shows the latest events. Only events that happened while the page was open are shown.

## Cameras

<div class="wt-shot" markdown>![The cameras page](assets/screenshots/cameras.webp)</div>

Every camera, with its status, the worker running it, the frames per second actually being analysed, its
delivery mode and when it last changed. Use the **search box** to find cameras by name, location, address or
worker, and the **All / Running / Issues / Stopped** buttons to filter.

The icons at the end of each row (operators and admins only):

| Icon | Action |
|---|---|
| Broadcast | Watch this camera live in a large player. Shown only while it is running. |
| Pencil | Edit the camera. |
| Square | Stop analysing the camera. It stays in the list as *stopped*. |
| Play | Start a stopped camera, or **Retry** a failed one. |
| Bin | Delete the camera. You are asked to confirm. |

Below a camera's status you may see *Attempt 2* (how many times it failed to start recently) and, in red, the
last error, such as *cannot open source*.

The [Cameras guide](cameras.md) explains the statuses and what to do about each one.

### Adding or editing a camera

<div class="wt-shot" markdown>![The add camera dialog](assets/screenshots/add-camera.webp)</div>

Click **Add camera** (or the pencil on a camera's row). The form has four parts:

- **Camera**: a name, an optional location, and the **source URL** the worker opens, for example
  `rtsp://user:password@10.0.0.12:554/stream`. When you edit a camera the source field is empty on purpose: the
  password is never sent back to the browser. Leave it empty to keep the current address.
- **Detection**: how many frames per second to analyse, the detector size and the minimum confidence.
- **Results**: how often to send a result per face, which picture to send, the delivery mode, an optional
  results URL just for this camera, and whether to include the full frame.
- **Metadata**: optional notes in JSON, for example `{"site": "north", "zone": "entrance"}`, for your own use.

Every field is explained in [Cameras](cameras.md#camera-settings). Saving a change to the source or to any
setting restarts that camera's analysis, which causes a short gap of a few seconds.

## Live view

<div class="wt-shot" markdown>![The live video wall](assets/screenshots/live.webp)</div>

A wall with one tile for every running or reconnecting camera. Press play on a tile, or **Play all** at the top,
to start watching; **Stop all** stops every player. Each tile shows the camera's name, location, worker and
current frame rate, and a red **LIVE** badge while video is playing.

Video goes directly from the worker to your browser. If a tile says the connection failed, your browser can't
reach that worker's live video address; see [Workers](workers.md#live-view-from-a-worker).

Players stop when you leave the page, so an open Live view tab doesn't keep using bandwidth after you move on.

## Workers

<div class="wt-shot" markdown>![The workers page](assets/screenshots/workers.webp)</div>

One card per worker. Each card shows:

- the worker's **name**, its id and its software version, and whether it is *online*, *suspect* or *offline*
  (plus *draining* when it is being drained);
- **Cameras**: slots in use out of its capacity;
- **CPU** and **Memory** use of the machine, turning orange above 65% and red above 85%;
- **Sent, Dropped, Failed, Queued**: result delivery counters since the worker started. *Queued* is the number of
  results waiting to be sent, which grows when your results application is down;
- the machine's name, its number of processor cores, and when it last checked in.

Buttons:

- **Drain** (operators and admins) moves all of the worker's cameras to other workers and keeps new ones away,
  for example before you reboot the machine. The button then says **Undrain**.
- **Forget worker** (the bin, admins only, shown only when the worker isn't online) removes a worker you have
  retired from the list. A machine that still has a valid worker token can register again later; revoke its token
  to keep it out for good.

The [Workers guide](workers.md) explains how to add workers and how to size them.

## Activity

<div class="wt-shot" markdown>![The activity page](assets/screenshots/activity.webp)</div>

A live, plain-language feed of everything that happens: cameras added, assigned to a worker, started, stopped,
reconnecting or failed; workers joining, missing check-ins, going offline or being drained. Errors also pop up as
red notices in the corner of the screen, whichever page you are on.

The feed only contains events from the time this browser tab has been open. For a permanent record of changes,
use the Audit log.

## Audit log

Admins see an **Audit log** page: every change made through the dashboard or the API, newest first, with when it
happened, who made it and with which role, what was done, and to which camera or worker. Camera passwords are
hidden. Entries are kept for a year by default (`AUDIT_RETENTION_DAYS`).

## What each role sees

| | Viewer | Operator | Admin |
|---|---|---|---|
| Overview, Cameras, Workers, Activity | yes | yes | yes |
| Watch live video | yes | yes | yes |
| Add, edit, start, stop and delete cameras | | yes | yes |
| Drain and undrain workers | | yes | yes |
| Forget workers | | | yes |
| Audit log | | | yes |

Roles come from your token or from your identity provider; see [Users and sign-in](access.md).
