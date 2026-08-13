import { run as runTui, type TuiInput } from "@medusa-ai/tui"
import { Global } from "@medusa-ai/core/global"
import { AppNodeBuilder } from "@medusa-ai/core/effect/app-node-builder"
import { Effect } from "effect"

export function run(input: TuiInput) {
  return runTui(input).pipe(Effect.provide(AppNodeBuilder.build(Global.node)))
}
