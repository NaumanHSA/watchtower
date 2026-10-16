# How Watchtower works

This page explains Watchtower in plain terms: what the pieces are, what happens when you add a camera, and what
happens when something breaks. Read it once before you install anything; the rest of the manual will make a lot
more sense.

## The short version

You tell Watchtower which cameras to watch. Watchtower decides which machine analyses each camera, starts the
analysis, keeps it running, and sends every face it finds to your application. You never assign a camera to a
machine yourself, and you never restart anything by hand when a machine fails.

## The pieces

![Your team uses the controller; workers sync with the controller, read cameras, send results to your application and live video to viewers](assets/architecture.svg)

Watchtower is made of a few services. In a small setup they can all run on one computer; in a large one they are
spread over many machines and sites.

**The controller** is the brain. It keeps the list of cameras and their settings, keeps track of every worker,
and decides which worker runs which camera. It also serves the web dashboard and the REST API, and checks who is
allowed to do what. It stores everything in **MongoDB**. You run one controller, or several copies of it behind
a load balancer if you need high availability.

**Workers** do the heavy lifting. A worker is a machine (a server, a small edge box, a virtual machine) that
reads video from cameras, finds and follows faces, and sends the results to your application. Each worker says
how many cameras it can handle; that number is its *capacity*, or its number of *slots*. To handle more cameras
you add more workers. Every worker runs next to a copy of **MediaMTX**, an open-source video server that pulls
the video from each camera once and shares it with the analysis and with anyone watching live.

**An identity service** decides who may sign in. Watchtower doesn't keep its own user accounts. You can use the
small **token server** that ships with Watchtower, your company's single sign-on (anything that speaks OpenID
Connect: Keycloak, Microsoft Entra ID, Auth0, Okta and others), a fixed list of tokens in the controller's
configuration, or your own service. See [Users and sign-in](access.md).

**Your application** receives the results. It is anything with an HTTPS address that accepts a `POST` request:
an access control system, an analytics pipeline, a search index, an alerting service. Watchtower calls it the
*results URL*. See [Receiving results](results.md).

## Workers call the controller, never the other way round

This is the most important idea in Watchtower, and it is why workers can live almost anywhere.

A worker is started with just two settings: the controller's address (`CONTROLLER_URL`) and a token that proves
it is allowed to be a worker (`WATCHTOWER_TOKEN`). When it starts, it introduces itself to the controller
(*registration*). From then on it checks in every five seconds (a *sync*). Each sync carries a short report:
which cameras the worker is running, how each one is doing, how busy the machine is. The controller's answer is
the complete list of cameras the worker *should* be running, with their settings. The worker then starts or
stops whatever it needs to so that its reality matches that list.

Because the worker always makes the call, the controller never needs to reach a worker. Workers can sit behind
NAT, behind a firewall that allows only outgoing traffic, on a shop floor or in another country. The only
incoming traffic a worker host ever needs is for people watching live video, and that is optional.

## What happens when you add a camera

```mermaid
sequenceDiagram
    autonumber
    actor You
    participant C as Controller
    participant S as Scheduler
    participant W as Worker
    participant A as Your application
    You->>C: Add camera in the dashboard or the API
    C-->>You: Saved, status pending
    S->>S: Choose the online worker with the most room
    W->>C: Sync, here is what I run and how busy I am
    C-->>W: Please also run the new camera
    W->>W: Start analysing it and offer it for live view
    W->>C: Next sync, the camera is running at 8 fps
    W->>A: A result for each face it sees
```

1. **You add the camera** in the dashboard or with one API call. You give it a name and a source URL, such as
   `rtsp://user:password@10.0.0.12:554/stream`. The controller saves it with the status **pending**.
2. **The scheduler picks a worker.** The scheduler is a part of the controller that runs every couple of seconds.
   It looks for cameras that need a worker and chooses, among the workers that are online and not being drained
   for maintenance, the one with the lowest share of its slots in use. The camera's status becomes
   **starting**.
3. **The worker learns about it on its next sync**, usually within five seconds. It asks its MediaMTX to pull the
   camera, starts a separate analysis process (an *engine*) for it, and reports **running** once frames are
   flowing.
4. **Results start flowing.** The engine looks at a few frames per second (8 by default), finds faces with the
   YuNet face detector, and follows each face from frame to frame so that one person walking past produces a
   handful of results instead of hundreds. Each result contains the best picture of that face so far and is sent
   to your results URL.

The whole journey from clicking **Add camera** to the first result usually takes five to ten seconds.

## What a result looks like

Watchtower does not send one message per video frame. It follows each face while it is in view and sends:

- one result as soon as it is sure the face is real (it has been seen in several frames in a row),
- another one at most once per *result interval* (one second by default) while the face stays in view,
- a final one when the face leaves, marked as the end of that track.

Each result carries the face crop, optionally the full frame, where the face is in the picture, how confident
the detector was, which camera it came from, and a unique id. You choose per camera whether results should be
sent in **real time** (never late, but old results are dropped if your application can't keep up) or
**reliably** (stored on the worker's disk and delivered in order, never lost). Results can be encrypted so that
only your application can read them. The details are in [Receiving results](results.md).

Watchtower detects and tracks faces. It does not recognise who someone is; if you need that, your application
does it with the face crops Watchtower sends.

## When things go wrong

Watchtower is built to keep cameras running without anyone watching it. Here is what it does on its own:

| What happens | What Watchtower does |
|---|---|
| A camera stops sending video | The camera shows **reconnecting** and the engine keeps trying. If the camera stays away, the worker gives up on it and the scheduler tries again later, if possible on another worker, waiting longer each time (10 seconds, then 20, 40 and so on, up to 10 minutes). After five failed starts the camera is marked **failed** and waits for you to press **Retry**. |
| An analysis process crashes | The worker restarts it, waiting a little longer each time. |
| A worker crashes, loses power or loses its network | After about 15 seconds without a sync the worker is marked **suspect**. After 75 seconds it is **offline** and its cameras move to other workers that have free slots. |
| A worker is shut down normally | It tells the controller it is leaving; its cameras move to other workers right away. |
| The controller or MongoDB goes down | Workers keep analysing their cameras and keep sending results. When the controller is back, everything catches up on the next sync. |
| Your results application goes down | In real-time mode, results that are too old are dropped so that fresh ones arrive first when it is back. In reliable mode, results wait on the worker's disk and are delivered in order. |
| You need to service a worker | **Drain** it in the dashboard. Its cameras move to other workers and it gets no new ones until you undrain it. |

## Who can do what

Everyone who uses Watchtower has one of three roles. A **viewer** can see everything and watch live video. An
**operator** can also add, change, start, stop and delete cameras and drain workers. An **admin** can do
everything, including removing workers from the list and reading the audit log. Machines that run workers use a
fourth role, **worker**, which can only register and sync.

## Where to go next

- To see all of this for yourself in ten minutes, follow [Try it on your computer](quick-start.md).
- To learn your way around the web interface, take [the dashboard tour](dashboard.md).
- When you are ready for real cameras and machines, read [Deploying to production](deployment.md).
