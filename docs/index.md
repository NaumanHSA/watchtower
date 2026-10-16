---
title: Watchtower
description: Real-time face detection across every camera, on workers you control.
hide:
  - navigation
  - toc
---

<div class="wt-hero" markdown>

# Watchtower <span>keeps an eye on every camera</span>

Watchtower runs **face detection on many camera streams across many machines** and sends each detection to your
application as it happens. You add cameras in a web dashboard or through an API; Watchtower decides which machine
analyses each one, keeps them all running through failures, and lets your team watch any camera live.

[Try it in 10 minutes](quick-start.md){ .md-button .md-button--primary }
[How it works](how-it-works.md){ .md-button }
[View on GitHub](https://github.com/NaumanHSA/watchtower){ .md-button }

</div>

## Start here

This site is a complete manual. If you are new to Watchtower, read it in this order:

<div class="grid cards wt-steps" markdown>

-   :material-numeric-1-circle:{ .lg .middle } **Understand the idea**

    ---

    What the controller, workers and results are, what happens when you add a camera, and what Watchtower does
    when something breaks. A ten-minute read.

    [:octicons-arrow-right-24: How Watchtower works](how-it-works.md)

-   :material-numeric-2-circle:{ .lg .middle } **Run it on your computer**

    ---

    Start everything with one Docker command, add cameras, watch them live, see results, add a second worker and
    watch Watchtower survive a crash.

    [:octicons-arrow-right-24: Try it on your computer](quick-start.md)

-   :material-numeric-3-circle:{ .lg .middle } **Learn the dashboard**

    ---

    Every page and button of the web interface explained, and what each role can see.

    [:octicons-arrow-right-24: A tour of the dashboard](dashboard.md)

-   :material-numeric-4-circle:{ .lg .middle } **Go to production**

    ---

    A step-by-step installation with HTTPS, real tokens, workers at your sites, and a results receiver, ending with
    a checklist.

    [:octicons-arrow-right-24: Deploying to production](deployment.md)

</div>

## What Watchtower does

<div class="grid cards" markdown>

-   :material-earth:{ .lg .middle } **Workers anywhere**

    ---

    Workers only make outgoing connections, so they run behind NAT, on small edge boxes and in any cloud.

    [:octicons-arrow-right-24: Workers](workers.md)

-   :material-autorenew:{ .lg .middle } **A fleet that heals itself**

    ---

    Cameras are spread over workers automatically, and moved when a worker fails or is drained for maintenance.

    [:octicons-arrow-right-24: When things go wrong](how-it-works.md#when-things-go-wrong)

-   :material-lightning-bolt:{ .lg .middle } **Real time or never lost**

    ---

    Per camera, choose results that are never late or results that are never lost.

    [:octicons-arrow-right-24: Receiving results](results.md)

-   :material-lock:{ .lg .middle } **Encrypted results**

    ---

    Results are encrypted on the worker so that only your application can read them.

    [:octicons-arrow-right-24: Encryption](results.md#encryption)

-   :material-card-account-details:{ .lg .middle } **Your sign-in, your roles**

    ---

    Single sign-on with your identity provider, the bundled token server, or your own service.

    [:octicons-arrow-right-24: Users and sign-in](access.md)

-   :material-access-point:{ .lg .middle } **Live view in the browser**

    ---

    Watch any camera, or a wall of them, straight from the worker over WebRTC.

    [:octicons-arrow-right-24: Live view](workers.md#live-view-from-a-worker)

</div>

Watchtower **detects and tracks** faces and hands you the best picture of each one. It doesn't identify people and
it isn't a video recorder. Please read [Privacy](privacy.md) before you point it at people.

## See it

=== "Overview"

    <div class="wt-shot" markdown>![Overview: fleet health, cameras needing attention, workers and recent activity](assets/screenshots/overview.webp)</div>

=== "Cameras"

    <div class="wt-shot" markdown>![Cameras: search, filter, start, stop, edit](assets/screenshots/cameras.webp)</div>

=== "Live view"

    <div class="wt-shot" markdown>![Live view: a wall of WebRTC players](assets/screenshots/live.webp)</div>

=== "Workers"

    <div class="wt-shot" markdown>![Workers: capacity, CPU, memory and delivery counters](assets/screenshots/workers.webp)</div>

=== "Add camera"

    <div class="wt-shot" markdown>![Add camera dialog with detection and delivery settings](assets/screenshots/add-camera.webp)</div>

=== "Sign in"

    <div class="wt-shot" markdown>![Sign in with single sign-on or a token](assets/screenshots/sign-in.webp)</div>

## The manual

| Section | What's in it |
|---|---|
| **Get started** | [How Watchtower works](how-it-works.md), [Try it on your computer](quick-start.md), [A tour of the dashboard](dashboard.md) |
| **User guide** | [Cameras](cameras.md): connecting cameras and every camera setting. [Workers](workers.md): adding, sizing, live view, maintenance. [Receiving results](results.md): receivers, delivery modes, encryption. [Users and sign-in](access.md): roles, tokens, single sign-on. |
| **Deploy** | [Deploying to production](deployment.md), [Running on Kubernetes](kubernetes.md), [Monitoring and maintenance](operations.md) |
| **Reference** | [Configuration](configuration.md): every setting. [REST API](api.md). [Token servers](token-server.md): the sign-in contract. [Architecture](architecture.md): the design in depth. |
| **Help** | [Troubleshooting](troubleshooting.md), [Privacy](privacy.md) |

## The quickest look

If you have Docker, this starts everything on your computer:

```bash
git clone https://github.com/NaumanHSA/watchtower.git
cd watchtower
docker compose -f deploy/compose/dev/compose.yml up --build -d
```

Then open <http://localhost:7000> and sign in with `dev-admin-token`.
[Try it on your computer](quick-start.md) takes it from there.
