# AI Assistant: What Is Sent and Kept

The AI assistant is part of OpenAVC Cloud. It runs in the Programmer of a system paired to a
cloud account, and only when someone opens it and asks something. A system that is not paired,
or where nobody uses the assistant, sends nothing to an AI provider. This page covers what is
sent, to whom, what is kept, and how changes it makes can be undone. How usage is measured is in
[How AI Usage Works](how-ai-usage-works.md).

## What is sent to Anthropic

The assistant runs on Anthropic's Claude API. Each request sends Anthropic:

- The conversation so far: your messages and the assistant's replies.
- An overview of the project: device names, drivers and addresses, device groups, variables,
  macros and their triggers, pages, and scripts.
- Whatever is read from or done on the system while answering: device details and states, a
  macro, page, script or driver, recent log lines, and the result of each change or command.

What is never sent:

- Device logins saved on the system (OpenAVC 0.40.0 and later). The assistant cannot read,
  enter or change one: logins are entered on the device's page in the Programmer. Plugin logins
  are shown to it as `***`.
- The Programmer password or API key, and the system's cloud key.

Anything typed into the conversation is sent as typed, a password included. The assistant is
instructed never to ask for a login in the chat.

## What Anthropic does with it

Under Anthropic's terms for its API, inputs and outputs are not used to train its models, and
Anthropic deletes them within 30 days, unless it must keep them longer to enforce its usage
policy or to comply with the law. See Anthropic's
[data retention](https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data)
and [model training](https://privacy.claude.com/en/articles/7996868-is-my-data-used-for-model-training)
pages. Anthropic is listed among OpenAVC's service providers in the
[privacy policy](https://openavc.com/privacy).

## What OpenAVC keeps

- **Conversations:** your messages, the replies, and what was read from and changed on the
  system to answer them, so you can come back to a conversation. Each is kept until someone
  deletes it from the AI Assistant's conversation list in the Programmer, or the account is
  deleted. When a person is removed from an account, their conversations are deleted 30 days
  later.
- **Usage records:** the tokens and cost of each request, kept 24 months, for billing and usage
  limits.

Both are included in the account's data export (**My Account > Your data** in the portal).
Everything else OpenAVC Cloud keeps is in
[OpenAVC Cloud Security and Data Handling](cloud-security-and-data.md).

## Who can use it

Anyone who can open that system's Programmer, on site or through remote access from the portal.

## Changes it makes

The assistant uses the same actions an administrator has in the Programmer: it edits the
project, sends device commands, writes scripts, and installs drivers and plugins. Each action is
recorded in the conversation. Before its first change in a conversation, the system takes a
project backup named "Before AI changes". It is in the **Backups** list at the bottom of the
Program view and restores like any other.

## Turning it off

Leave the system unpaired, or don't use the assistant. Nothing is sent to Anthropic until someone
asks it something.

## Related

- [How AI Usage Works](how-ai-usage-works.md)
- [OpenAVC Cloud Security and Data Handling](cloud-security-and-data.md)
- [Network & Security Cut Sheet](it-network-guide.md): what the cloud can do on a paired system
