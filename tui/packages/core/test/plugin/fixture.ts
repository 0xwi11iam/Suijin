import { AgentV2 } from "@medusa-ai/core/agent"
import { AISDK } from "@medusa-ai/core/aisdk"
import { Catalog } from "@medusa-ai/core/catalog"
import { CommandV2 } from "@medusa-ai/core/command"
import { Credential } from "@medusa-ai/core/credential"
import { AppNodeBuilder } from "@medusa-ai/core/effect/app-node-builder"
import { LayerNodePlatform } from "@medusa-ai/core/effect/app-node-platform"
import { LayerNode } from "@medusa-ai/core/effect/layer-node"
import { EventV2 } from "@medusa-ai/core/event"
import { FileSystem } from "@medusa-ai/core/filesystem"
import { FSUtil } from "@medusa-ai/core/fs-util"
import { Integration } from "@medusa-ai/core/integration"
import { Location } from "@medusa-ai/core/location"
import { Npm } from "@medusa-ai/core/npm"
import { PluginV2 } from "@medusa-ai/core/plugin"
import { Reference } from "@medusa-ai/core/reference"
import { SkillV2 } from "@medusa-ai/core/skill"
import { Effect, Layer } from "effect"
import { tempLocationLayer } from "../fixture/location"

const npmLayer = Layer.succeed(
  Npm.Service,
  Npm.Service.of({
    add: () => Effect.succeed({ directory: "", entrypoint: undefined }),
    install: () => Effect.void,
    which: () => Effect.succeed(undefined),
  }),
)

export const PluginTestLayer = AppNodeBuilder.build(
  LayerNode.group([
    FileSystem.node,
    FSUtil.node,
    Location.node,
    Npm.node,
    Credential.node,
    EventV2.node,
    LayerNodePlatform.httpClient,
    PluginV2.node,
    AgentV2.node,
    AISDK.node,
    Catalog.node,
    CommandV2.node,
    Integration.node,
    Reference.node,
    SkillV2.node,
  ]),
  [
    [Location.node, tempLocationLayer],
    [Npm.node, npmLayer],
  ],
)
