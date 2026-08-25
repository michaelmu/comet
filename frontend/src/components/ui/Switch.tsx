import * as SwitchPrimitive from "@radix-ui/react-switch";
import { useId } from "react";

interface SwitchProps {
  checked: boolean;
  className?: string | undefined;
  compact?: boolean;
  disabled?: boolean;
  hint?: string | undefined;
  label: string;
  onCheckedChange: (checked: boolean) => void;
}

export function Switch({
  checked,
  className,
  compact = false,
  disabled = false,
  hint,
  label,
  onCheckedChange,
}: SwitchProps) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const fieldClassName = `switch-field${compact ? " switch-field--compact" : ""}${className ? ` ${className}` : ""}`;
  return (
    <div className={fieldClassName}>
      <label className={compact ? "visually-hidden" : undefined} htmlFor={id}>
        {label}
      </label>
      <SwitchPrimitive.Root
        aria-describedby={hintId}
        checked={checked}
        className="switch"
        disabled={disabled}
        id={id}
        onCheckedChange={onCheckedChange}
      >
        <SwitchPrimitive.Thumb className="switch__thumb" />
      </SwitchPrimitive.Root>
      {hint ? (
        <span className="field__hint switch-field__hint" id={hintId}>
          {hint}
        </span>
      ) : null}
    </div>
  );
}
