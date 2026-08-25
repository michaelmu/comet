import { GripVertical } from "lucide-react";
import {
  Fragment,
  type KeyboardEvent,
  type PointerEvent,
  type ReactNode,
  useId,
  useState,
} from "react";
import { Button } from "../../components/ui/Button";

interface RenderState {
  dragging: boolean;
  handle: ReactNode;
}

export function ReorderableList<Item>({
  className = "",
  getId,
  getLabel,
  items,
  label,
  onChange,
  renderItem,
}: {
  className?: string;
  getId: (item: Item) => string;
  getLabel?: (item: Item) => string;
  items: readonly Item[];
  label: string;
  onChange: (items: Item[]) => void;
  renderItem: (item: Item, index: number, state: RenderState) => ReactNode;
}) {
  const listId = useId();
  const [drag, setDrag] = useState<{ id: string; target: number; x: number; y: number } | null>(
    null,
  );
  const [announcement, setAnnouncement] = useState("");
  const move = (itemId: string, target: number) => {
    const index = items.findIndex((item) => getId(item) === itemId);
    if (index < 0 || target < 0 || target >= items.length || index === target) return;
    const next = [...items];
    const [entry] = next.splice(index, 1);
    if (entry === undefined) return;
    next.splice(target, 0, entry);
    onChange(next);
    setAnnouncement(`${getLabel?.(entry) ?? getId(entry)}: ${target + 1} / ${items.length}`);
  };
  const moveFromPointer = (event: PointerEvent<HTMLButtonElement>) => {
    if (!drag) return;
    event.preventDefault();
    const row = document
      .elementFromPoint(event.clientX, event.clientY)
      ?.closest<HTMLElement>("[data-reorder-id]");
    const targetId = row?.dataset.reorderId;
    const target = targetId ? items.findIndex((item) => getId(item) === targetId) : drag.target;
    setDrag({
      ...drag,
      target: target >= 0 ? target : drag.target,
      x: event.clientX,
      y: event.clientY,
    });
  };
  const handleKey = (event: KeyboardEvent<HTMLButtonElement>, itemId: string) => {
    if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
    event.preventDefault();
    const index = items.findIndex((item) => getId(item) === itemId);
    move(itemId, index + (event.key === "ArrowUp" ? -1 : 1));
  };
  const draggedIndex = drag ? items.findIndex((entry) => getId(entry) === drag.id) : -1;
  const dragged = draggedIndex >= 0 ? items[draggedIndex] : undefined;

  return (
    <Fragment>
      <ul aria-label={label} className={`reorder-list ${className}`.trim()} id={listId}>
        {items.map((item, index) => {
          const itemId = getId(item);
          const dragging = drag?.id === itemId;
          const target =
            drag && !dragging && drag.target === index
              ? drag.target < draggedIndex
                ? "before"
                : "after"
              : null;
          const handle = (
            <Button
              aria-label={`${label}: ${getLabel?.(item) ?? itemId} (${index + 1}/${items.length})`}
              aria-pressed={dragging}
              className="reorder-list__handle"
              onKeyDown={(event) => handleKey(event, itemId)}
              onLostPointerCapture={() => setDrag(null)}
              onPointerDown={(event) => {
                if (event.button !== 0) return;
                event.currentTarget.setPointerCapture(event.pointerId);
                setDrag({ id: itemId, target: index, x: event.clientX, y: event.clientY });
              }}
              onPointerMove={moveFromPointer}
              onPointerUp={(event) => {
                if (drag) move(drag.id, drag.target);
                if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                  event.currentTarget.releasePointerCapture(event.pointerId);
                }
                setDrag(null);
              }}
              title={label}
              variant="ghost"
            >
              <GripVertical aria-hidden="true" size={18} />
            </Button>
          );
          return (
            <li
              className={[
                "reorder-list__item",
                dragging ? "reorder-list__item--dragging" : "",
                target ? `reorder-list__item--drop-${target}` : "",
              ]
                .filter(Boolean)
                .join(" ")}
              data-reorder-id={itemId}
              key={itemId}
            >
              {renderItem(item, index, { dragging, handle })}
            </li>
          );
        })}
      </ul>
      <span aria-live="polite" className="visually-hidden">
        {announcement}
      </span>
      {drag ? (
        <div
          aria-hidden="true"
          className="reorder-list__preview"
          style={{ left: drag.x, top: drag.y }}
        >
          <GripVertical size={16} />
          {(dragged === undefined ? undefined : getLabel?.(dragged)) ?? label}
        </div>
      ) : null}
    </Fragment>
  );
}
