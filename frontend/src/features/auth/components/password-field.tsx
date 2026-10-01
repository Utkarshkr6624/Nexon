import { Eye, EyeOff } from 'lucide-react'
import { useState } from 'react'
import type { JSX, Ref } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import { MAX_STRENGTH_SCORE, STRENGTH_TOKENS } from '../password-strength'
import { usePasswordRules } from '../use-password-rules'
import { PasswordRulesChecklist } from './password-rules-checklist'

export interface PasswordFieldProps {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  autoComplete?: string
  placeholder?: string
  error?: string | undefined
  disabled?: boolean
  required?: boolean
  /** Render the Weak/Fair/Good/Strong meter under the field. */
  showStrength?: boolean
  /** Render the live rule checklist under the meter. */
  showRules?: boolean
  onBlur?: () => void
  /**
   * Handle on the underlying input. Optional and additive: a form that
   * validates on submit needs to move focus to the first offending field, and
   * the field it owns does not otherwise expose a way to do that.
   */
  inputRef?: Ref<HTMLInputElement>
}

/**
 * A labelled password input with a visibility toggle, an optional strength
 * meter and an optional live policy checklist.
 *
 * Deliberately dumb: the only state it owns is whether the value is currently
 * shown, because that is a property of the control and not of the form. Every
 * rule outcome is derived from `value` on each render, so the field can never
 * disagree with the form that owns the value.
 */
export function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete = 'current-password',
  placeholder,
  error,
  disabled = false,
  required = false,
  showStrength = false,
  showRules = false,
  onBlur,
  inputRef,
}: PasswordFieldProps): JSX.Element {
  const [visible, setVisible] = useState(false)
  const { strength, results } = usePasswordRules(value)

  const hasValue = value.length > 0
  const errorId = `${id}-error`
  const strengthId = `${id}-strength`
  const tokens = STRENGTH_TOKENS[strength.level]
  const showMeter = showStrength && hasValue

  // The level is named in text beside the bar, so the meter never relies on
  // colour alone; pointing the input's description at that label is what makes
  // the reading available at the moment of focus rather than on sight.
  const describedBy = [error ? errorId : null, showMeter ? strengthId : null]
    .filter(Boolean)
    .join(' ')

  return (
    <div className="app-form-field">
      <Label htmlFor={id}>{label}</Label>

      <Input
        ref={inputRef}
        id={id}
        type={visible ? 'text' : 'password'}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onBlur={onBlur}
        autoComplete={autoComplete}
        placeholder={placeholder}
        disabled={disabled}
        required={required}
        error={Boolean(error)}
        aria-describedby={describedBy || undefined}
        endAdornment={
          <Button
            type="button"
            variant="ghost"
            size="icon"
            disabled={disabled}
            // A toggle button's name states the thing it controls, not the
            // current state: `aria-pressed` is the state channel. Renaming it
            // per click would announce the state twice and contradict itself.
            aria-label="Show password"
            aria-pressed={visible}
            onClick={() => setVisible((current) => !current)}
            className="size-7 rounded-md text-muted-foreground transition-colors duration-150 hover:bg-accent hover:text-foreground"
          >
            {visible ? (
              <EyeOff aria-hidden="true" />
            ) : (
              <Eye aria-hidden="true" />
            )}
          </Button>
        }
      />

      {/*
        The meter arrives on the first keystroke rather than sitting there
        reading "Weak" about an empty field — an empty input is not a weak
        password, and a red bar nobody earned is just noise.
      */}
      {showMeter && (
        <div className="flex items-center gap-2.5 pt-0.5">
          <span
            id={strengthId}
            className={cn(
              'w-14 shrink-0 text-[11px] font-semibold uppercase tracking-wide',
              tokens.text,
            )}
          >
            {tokens.label}
          </span>
          <Progress
            value={strength.score}
            max={MAX_STRENGTH_SCORE}
            indicatorClassName={cn(tokens.bar, 'duration-150 ease-out')}
            aria-label={`Password strength: ${tokens.label}`}
            className="h-1.5 flex-1"
          />
        </div>
      )}

      {showRules && <PasswordRulesChecklist results={results} className="pt-0.5" />}

      {error && (
        <p id={errorId} className="app-form-error">
          {error}
        </p>
      )}
    </div>
  )
}
