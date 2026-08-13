export * as PublicEventManifest from "./public-event-manifest"

import { Event } from "@medusa-ai/schema/event"
import { EventManifest } from "@medusa-ai/schema/event-manifest"

export const Definitions = EventManifest.ServerDefinitions
export const Latest = Event.latest(Definitions)
