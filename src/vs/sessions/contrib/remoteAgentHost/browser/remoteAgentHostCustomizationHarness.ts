/*---------------------------------------------------------------------------------------------
 *  Copyright (c) Microsoft Corporation. All rights reserved.
 *  Licensed under the MIT License. See License.txt in the project root for license information.
 *--------------------------------------------------------------------------------------------*/

import { Emitter, Event } from '../../../../base/common/event.js';
import { Disposable } from '../../../../base/common/lifecycle.js';
import { URI } from '../../../../base/common/uri.js';
import { Codicon } from '../../../../base/common/codicons.js';
import { OS } from '../../../../base/common/platform.js';
import { ThemeIcon } from '../../../../base/common/themables.js';
import { CancellationToken } from '../../../../base/common/cancellation.js';
import { autorun } from '../../../../base/common/observable.js';
import { localize } from '../../../../nls.js';
import { AICustomizationManagementSection, type IStorageSourceFilter } from '../../../../workbench/contrib/chat/common/aiCustomizationWorkspaceService.js';
import { PromptsStorage } from '../../../../workbench/contrib/chat/common/promptSyntax/service/promptsService.js';
import { PromptsType } from '../../../../workbench/contrib/chat/common/promptSyntax/promptTypes.js';
import { type IHarnessDescriptor, type ICustomizationItem, type ICustomizationItemProvider } from '../../../../workbench/contrib/chat/common/customizationHarnessService.js';
import { type IAgentPlugin, type IAgentPluginService, getCanonicalPluginCommandId } from '../../../../workbench/contrib/chat/common/plugins/agentPluginService.js';
import { isContributionEnabled } from '../../../../workbench/contrib/chat/common/enablement.js';
import { HOOK_METADATA } from '../../../../workbench/contrib/chat/common/promptSyntax/hookTypes.js';
import { formatHookCommandLabel } from '../../../../workbench/contrib/chat/common/promptSyntax/hookSchema.js';
import type { IAgentConnection } from '../../../../platform/agentHost/common/agentService.js';
import { ActionType } from '../../../../platform/agentHost/common/state/sessionActions.js';
import { type IAgentInfo, type ICustomizationRef, type ISessionCustomization, CustomizationStatus } from '../../../../platform/agentHost/common/state/sessionState.js';
import { BUILTIN_STORAGE } from '../../chat/common/builtinPromptsStorage.js';
import { AgentCustomizationSyncProvider } from '../../../../workbench/contrib/chat/browser/agentSessions/agentHost/agentCustomizationSyncProvider.js';

export { AgentCustomizationSyncProvider as RemoteAgentSyncProvider } from '../../../../workbench/contrib/chat/browser/agentSessions/agentHost/agentCustomizationSyncProvider.js';

/**
 * Maps a {@link CustomizationStatus} enum value to the string literal
 * expected by {@link ICustomizationItem.status}.
 */
function toStatusString(status: CustomizationStatus | undefined): 'loading' | 'loaded' | 'degraded' | 'error' | undefined {
	switch (status) {
		case CustomizationStatus.Loading: return 'loading';
		case CustomizationStatus.Loaded: return 'loaded';
		case CustomizationStatus.Degraded: return 'degraded';
		case CustomizationStatus.Error: return 'error';
		default: return undefined;
	}
}

/**
 * Provider that exposes a remote agent's customizations as
 * {@link ICustomizationItem} entries for the list widget.
 *
 * Baseline items come from {@link IAgentInfo.customizations} (available
 * without an active session). When a session is active, the provider
 * overlays {@link ISessionCustomization} data, which includes loading
 * status and enabled state.
 *
 * Each AHP {@link ICustomizationRef} represents a whole plugin (an Open
 * Plugins URI). To populate the per-type sections (Skills, Agents, Hooks,
 * Instructions, Prompts) we look up the matching local plugin via
 * {@link IAgentPluginService} and expand it into one item per contained
 * resource. Plugins not installed locally fall back to a single
 * placeholder entry that won't show up in any prompt-type section.
 */
export class RemoteAgentCustomizationItemProvider extends Disposable implements ICustomizationItemProvider {
	private readonly _onDidChange = this._register(new Emitter<void>());
	readonly onDidChange: Event<void> = this._onDidChange.event;

	private _agentCustomizations: readonly ICustomizationRef[];
	private _sessionCustomizations: readonly ISessionCustomization[] | undefined;

	constructor(
		agentInfo: IAgentInfo,
		connection: IAgentConnection,
		private readonly _agentPluginService: IAgentPluginService,
	) {
		super();
		this._agentCustomizations = agentInfo.customizations ?? [];

		// Listen for customization changes from any session via action events
		this._register(connection.onDidAction(envelope => {
			if (envelope.action.type === ActionType.SessionCustomizationsChanged) {
				const customizations = (envelope.action as { customizations?: ISessionCustomization[] }).customizations;
				if (customizations && customizations !== this._sessionCustomizations) {
					this._sessionCustomizations = customizations;
					this._onDidChange.fire();
				}
			}
		}));

		// Refire when local plugin contents change so the expanded items
		// stay in sync with skills/agents/etc. discovered on disk.
		this._register(autorun(reader => {
			const plugins = this._agentPluginService.plugins.read(reader);
			for (const plugin of plugins) {
				plugin.enablement.read(reader);
				plugin.skills.read(reader);
				plugin.agents.read(reader);
				plugin.commands.read(reader);
				plugin.instructions.read(reader);
				plugin.hooks.read(reader);
			}
			this._onDidChange.fire();
		}));
	}

	/**
	 * Updates the baseline agent customizations (e.g. when root state
	 * changes and agent info is refreshed).
	 */
	updateAgentCustomizations(customizations: readonly ICustomizationRef[]): void {
		this._agentCustomizations = customizations;
		this._onDidChange.fire();
	}

	async provideChatSessionCustomizations(_token: CancellationToken): Promise<ICustomizationItem[]> {
		const localPlugins = this._agentPluginService.plugins.get();

		// When a session is active, prefer session-level data (includes status)
		if (this._sessionCustomizations) {
			const items: ICustomizationItem[] = [];
			for (const sc of this._sessionCustomizations) {
				const refUri = URI.isUri(sc.customization.uri) ? sc.customization.uri : URI.parse(sc.customization.uri);
				const status = toStatusString(sc.status);
				items.push(...this._expandRef(
					refUri,
					sc.customization.displayName,
					sc.customization.description,
					localPlugins,
					{ enabled: sc.enabled, status, statusMessage: sc.statusMessage },
				));
			}
			return items;
		}

		// Baseline: agent-level customizations (no status info)
		const items: ICustomizationItem[] = [];
		for (const ref of this._agentCustomizations) {
			const refUri = URI.isUri(ref.uri) ? ref.uri : URI.parse(ref.uri as unknown as string);
			items.push(...this._expandRef(
				refUri,
				ref.displayName,
				ref.description,
				localPlugins,
				{},
			));
		}
		return items;
	}

	/**
	 * Expands a single AHP plugin reference into the per-type customization
	 * items it contains. When the plugin is installed locally we read its
	 * resources from {@link IAgentPluginService}; otherwise a single
	 * placeholder is returned so the entry isn't silently dropped.
	 */
	private _expandRef(
		refUri: URI,
		refName: string,
		refDescription: string | undefined,
		localPlugins: readonly IAgentPlugin[],
		overlay: { enabled?: boolean; status?: 'loading' | 'loaded' | 'degraded' | 'error'; statusMessage?: string },
	): ICustomizationItem[] {
		const plugin = localPlugins.find(p => p.uri.toString() === refUri.toString());
		if (!plugin || !isContributionEnabled(plugin.enablement.get())) {
			// No local match (or disabled) — surface a single placeholder so
			// the entry is at least visible somewhere when statuses arrive.
			return [{
				uri: refUri,
				type: 'plugin',
				name: refName,
				description: refDescription,
				storage: PromptsStorage.plugin,
				enabled: overlay.enabled,
				status: overlay.status,
				statusMessage: overlay.statusMessage,
			}];
		}

		const items: ICustomizationItem[] = [];
		const baseEnabled = overlay.enabled !== false;

		const pushNamed = (uri: URI, name: string, type: PromptsType, description?: string) => {
			items.push({
				uri,
				type,
				name,
				description,
				storage: PromptsStorage.plugin,
				enabled: baseEnabled,
				status: overlay.status,
				statusMessage: overlay.statusMessage,
			});
		};

		for (const skill of plugin.skills.get()) {
			pushNamed(skill.uri, getCanonicalPluginCommandId(plugin, skill.name), PromptsType.skill);
		}
		for (const agent of plugin.agents.get()) {
			pushNamed(agent.uri, getCanonicalPluginCommandId(plugin, agent.name), PromptsType.agent);
		}
		for (const command of plugin.commands.get()) {
			pushNamed(command.uri, getCanonicalPluginCommandId(plugin, command.name), PromptsType.prompt);
		}
		for (const instruction of plugin.instructions.get()) {
			pushNamed(instruction.uri, getCanonicalPluginCommandId(plugin, instruction.name), PromptsType.instructions);
		}

		// Hooks are pre-expanded per command, matching how local plugin hooks
		// are surfaced by `PromptsServiceCustomizationItemProvider`. The list
		// widget does not re-parse plugin-storage hooks.
		for (const hookGroup of plugin.hooks.get()) {
			const hookMeta = HOOK_METADATA[hookGroup.type];
			for (const hook of hookGroup.hooks) {
				const cmdLabel = formatHookCommandLabel(hook, OS);
				const truncatedCmd = cmdLabel.length > 60 ? cmdLabel.substring(0, 57) + '...' : cmdLabel;
				items.push({
					uri: hookGroup.uri,
					type: PromptsType.hook,
					name: hookMeta?.label ?? hookGroup.originalId,
					description: truncatedCmd || localize('hookUnset', "(unset)"),
					storage: PromptsStorage.plugin,
					enabled: baseEnabled,
					status: overlay.status,
					statusMessage: overlay.statusMessage,
				});
			}
		}

		return items;
	}
}

/**
 * Creates a {@link IHarnessDescriptor} for a remote agent discovered via
 * the agent host protocol.
 *
 * The descriptor exposes the agent's server-provided customizations through
 * an {@link ICustomizationItemProvider} and allows the user to
 * select local customizations for syncing via an {@link ICustomizationSyncProvider}.
 */
export function createRemoteAgentHarnessDescriptor(
	harnessId: string,
	displayName: string,
	itemProvider: RemoteAgentCustomizationItemProvider,
	syncProvider: AgentCustomizationSyncProvider,
): IHarnessDescriptor {
	const allSources = [PromptsStorage.local, PromptsStorage.user, PromptsStorage.plugin, BUILTIN_STORAGE];
	const filter: IStorageSourceFilter = { sources: allSources };

	return {
		id: harnessId,
		label: displayName,
		icon: ThemeIcon.fromId(Codicon.remote.id),
		hiddenSections: [
			AICustomizationManagementSection.Models,
			AICustomizationManagementSection.McpServers,
		],
		hideGenerateButton: true,
		getStorageSourceFilter(_type: PromptsType): IStorageSourceFilter {
			return filter;
		},
		itemProvider,
		syncProvider,
	};
}
