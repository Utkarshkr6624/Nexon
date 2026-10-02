import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

import { PasswordField } from '@/features/auth/components/password-field'

/**
 * The password control: a visibility toggle, an optional strength meter and an
 * optional live checklist.
 *
 * Two properties are load-bearing and neither is visible in a screenshot. The
 * meter must state its level in *text*, because a coloured bar is invisible to
 * anyone with a colour vision deficiency or reading the page in sunlight. And
 * the toggle must never submit the form it lives in — it is inside a register or
 * change-password form, and a stray submit there means a half-filled password
 * goes to the server.
 */

function Harness({
  initial = '',
  showStrength = false,
  showRules = false,
  error,
  onSubmit,
}: {
  initial?: string
  showStrength?: boolean
  showRules?: boolean
  error?: string
  onSubmit?: () => void
}) {
  const [value, setValue] = useState(initial)
  return (
    <form onSubmit={(event) => { event.preventDefault(); onSubmit?.() }}>
      <PasswordField
        id="password"
        label="Password"
        value={value}
        onChange={setValue}
        error={error}
        showStrength={showStrength}
        showRules={showRules}
      />
      <button type="submit">Save</button>
    </form>
  )
}

describe('PasswordField visibility toggle', () => {
  it('flips the input type and reports the state through aria-pressed', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    const input = screen.getByLabelText('Password')
    const toggle = screen.getByRole('button', { name: 'Show password' })

    expect(input).toHaveAttribute('type', 'password')
    expect(toggle).toHaveAttribute('aria-pressed', 'false')

    await user.click(toggle)
    expect(input).toHaveAttribute('type', 'text')
    expect(screen.getByRole('button', { name: 'Show password' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(input).toHaveAttribute('type', 'password')
    expect(screen.getByRole('button', { name: 'Show password' })).toHaveAttribute(
      'aria-pressed',
      'false',
    )
  })

  it('keeps its name stable so the pressed state is announced once, not twice', () => {
    render(<Harness />)

    // Renaming the control per click would announce "Hide password, pressed"
    // and then contradict it with the pressed channel.
    expect(screen.getByRole('button', { name: 'Show password' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /hide/i })).not.toBeInTheDocument()
  })

  it('does not submit the form it is rendered inside', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()
    render(<Harness onSubmit={onSubmit} />)

    const toggle = screen.getByRole('button', { name: 'Show password' })
    // A `type="button"` toggle cannot submit; the attribute is the guarantee.
    expect(toggle).toHaveAttribute('type', 'button')

    await user.click(toggle)
    await user.click(toggle)
    expect(onSubmit).not.toHaveBeenCalled()

    // The form still submits when the user actually asks it to, so the previous
    // assertion is about the toggle rather than about a dead submit button.
    await user.click(screen.getByRole('button', { name: 'Save' }))
    expect(onSubmit).toHaveBeenCalledOnce()
  })
})

describe('PasswordField strength meter', () => {
  it('stays hidden until there is something to score', () => {
    render(<Harness showStrength />)

    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByText('Weak')).not.toBeInTheDocument()
  })

  it('states the level in text, not in colour alone', async () => {
    const user = userEvent.setup()
    render(<Harness showStrength />)

    const input = screen.getByLabelText('Password')
    await user.type(input, 'abc')
    expect(await screen.findByText('Weak')).toBeInTheDocument()

    await user.clear(input)
    await user.type(input, 'Passw0rd!')
    expect(await screen.findByText('Good')).toBeInTheDocument()

    await user.clear(input)
    await user.type(input, 'Str0ng!passphrase!')
    expect(await screen.findByText('Strong')).toBeInTheDocument()
  })

  it('names the bar for assistive technology and points the input at it', async () => {
    const user = userEvent.setup()
    render(<Harness showStrength />)

    await user.type(screen.getByLabelText('Password'), 'Passw0rd!')

    const meter = await screen.findByRole('progressbar')
    expect(meter).toHaveAttribute('aria-label', 'Password strength: Good')
    // The reading has to be available at the moment of focus, not only on sight.
    expect(screen.getByLabelText('Password')).toHaveAttribute(
      'aria-describedby',
      'password-strength',
    )
  })
})

describe('PasswordField error wiring', () => {
  it('marks the input invalid and describes it with the message', () => {
    render(<Harness error="Passwords do not match." />)

    const input = screen.getByLabelText('Password')
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(input).toHaveAttribute('aria-describedby', 'password-error')

    const describedBy = input.getAttribute('aria-describedby')
    expect(describedBy).not.toBeNull()
    const message = document.getElementById(describedBy as string)
    expect(message).toHaveTextContent('Passwords do not match.')
  })

  it('describes the input with the error and the strength reading together', async () => {
    const user = userEvent.setup()
    render(<Harness showStrength error="Meet every requirement in the checklist." />)

    await user.type(screen.getByLabelText('Password'), 'abc')

    const input = screen.getByLabelText('Password')
    expect(input.getAttribute('aria-describedby')?.split(' ').sort()).toEqual([
      'password-error',
      'password-strength',
    ])
    expect(document.getElementById('password-error')).toHaveTextContent(
      'Meet every requirement in the checklist.',
    )
  })

  it('leaves aria-invalid off and describes nothing when there is no error', () => {
    render(<Harness />)

    const input = screen.getByLabelText('Password')
    expect(input).not.toHaveAttribute('aria-invalid')
    expect(input).not.toHaveAttribute('aria-describedby')
  })

  it('disables the input and its toggle together', () => {
    render(
      <form>
        <PasswordField
          id="password"
          label="Password"
          value="abc"
          onChange={() => undefined}
          disabled
        />
      </form>,
    )

    expect(screen.getByLabelText('Password')).toBeDisabled()
    // A toggle that still responds while the field is locked reports a value
    // the user can no longer change.
    expect(screen.getByRole('button', { name: 'Show password' })).toBeDisabled()
  })
})