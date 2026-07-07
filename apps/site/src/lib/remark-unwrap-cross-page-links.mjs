import { visit } from "unist-util-visit";
import { docIdFromVFilePath, isCrossPageLink, markdownLinkText } from "./doc-link-target.mjs";

/**
 * Cross-page markdown links become plain text in prose; they surface as SmartLinks in
 * the Resources footer instead.
 *
 * @returns {(tree: import('mdast').Root, file: import('vfile').VFile) => void}
 */
export function remarkUnwrapCrossPageLinks() {
  return (tree, file) => {
    const docId = docIdFromVFilePath(file.history?.[0] ?? "");
    if (!docId) {
      return;
    }

    visit(tree, "link", (node, index, parent) => {
      if (!parent || index === undefined || typeof node.url !== "string") {
        return;
      }

      if (!isCrossPageLink(node.url, docId)) {
        return;
      }

      parent.children[index] = {
        type: "text",
        value: markdownLinkText(node),
      };
    });
  };
}
