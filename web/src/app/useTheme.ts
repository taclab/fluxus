import { applyTheme, themePref, type Theme } from './theme'

export function useTheme(): [Theme, (t: Theme) => void] {
  return [themePref.useValue(), applyTheme]
}
