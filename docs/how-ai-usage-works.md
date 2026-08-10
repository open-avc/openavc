# How AI Usage Works

The AI assistant is included with OpenAVC Cloud. There is no prompt counter in the
interface, no allowance to watch, and no usage pack to buy. This page explains what
is actually being measured behind that, because "included" without an explanation
invites the reasonable suspicion that something is being counted anyway.

## Why prompts are the wrong unit

The assistant is agentic. When you ask it to build a room, it does not answer once. It
reads your project, adds devices, writes macros, builds a panel page, sends real
commands to real equipment, reads what came back, and fixes what did not work. A single
sentence from you can be dozens of steps of work.

So counting prompts would measure almost nothing useful. Two people who each sent one
message could have consumed wildly different amounts of work, and the person who wrote
the shorter message might well be the one who consumed more. Any product that sells you
"500 AI prompts a month" is selling you a number that does not correspond to what you
get.

What we measure instead is the work itself. And the unit we size everything against is
a **complete room build**: taking one space from an empty project to a working,
tested system.

## What a complete room build is worth

A full commissioning is by far the most expensive thing you can ask for, and everything
else is small next to it. Measured against the same real system, one complete build is
worth roughly:

| Instead of one complete room build, you could ask for |
|---|
| About 45 troubleshooting questions ("why is the projector offline?") |
| About 20 macros written from a description |
| About 8 panel edits ("add a mute button to the audio page") |

Those are typical figures rather than guarantees, but the shape holds: ordinary
day-to-day use of the assistant is a rounding error against commissioning a space. If
you have built your rooms and now ask the assistant a handful of questions a month, you
are nowhere near the interesting part of the range.

## The surprising part: asking for more does not cost more

The instinct is that a big request is an expensive request. It is not what happens.

In testing, a deliberately enormous prompt that built roughly six times more project
than a normal room build (ten devices, thirty one macros, five pages) cost **less than
half** what the ordinary single-room build cost.

What drives the cost is **iteration**, not scope. The expensive builds are the ones
where the assistant tests its work against live equipment, finds something that did not
respond the way the driver said it would, and works the problem. Five identical
requests against an identical starting point varied by nearly a factor of three, and the
expensive one was not a different room. It was the same room taking a longer path.

This is worth knowing for a practical reason: if a build is going slowly and taking
many rounds, that is the assistant doing the work you actually wanted, not waste. And
if you want to be economical with it, the lever is not writing shorter requests. It is
giving it good information up front: the right driver, the correct addresses, and a
clear description of what the space is supposed to do.

## How much is included

Every paid system carries enough assistant usage for **several complete builds of that
system**, and the amount is sized against the most expensive build measured rather than
a typical one, so a difficult commissioning cannot run out partway through. Usage is
pooled across your account rather than tracked per system, which means a straightforward
room subsidises a difficult one instead of each room being on its own.

Free accounts get the assistant too, with a monthly allowance. It is the only feature
difference between a free account and a paid one.

We do not publish the figure in tokens or in dollars, and that is deliberate. Tokens are
not a unit anyone can plan with, and a dollar figure would invite you to do arithmetic
that the pooling makes wrong.

## If something is genuinely unusual

Two protections exist, and neither one is a cutoff:

- **A cadence limit** on how quickly requests can arrive. It is set far above the speed
  any person works at, so you will not encounter it. It exists to stop a script or a
  loop from hammering the assistant, not to slow you down.
- **A ceiling on sustained spend** far above any real usage pattern, which exists to
  catch runaway automation. If it ever trips, we would talk to you about it rather than
  silently switch the assistant off.

If you are doing something legitimately heavy, such as commissioning a very large
deployment in a short window, tell us and we will raise your account's allocation. That
is a conversation, not a purchase.

## What is not counted

- Using OpenAVC itself. The software is free and open source, and the assistant is a
  cloud feature. Everything you can do in the Programmer without the assistant costs
  nothing at all, forever.
- The assistant reading your project. Looking at your devices, macros, and pages to
  answer a question is part of answering it.
- Retries after a failure on our side.

## Related

- [Programmer Overview](programmer-overview.md) for what the assistant can reach
- [Getting Started](getting-started.md) for pairing a system to the cloud
