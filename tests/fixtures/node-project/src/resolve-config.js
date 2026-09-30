export function resolveConfig(defaults, repository, task) {
  // Known fixture defect: repository values override task values.
  return { ...defaults, ...task, ...repository };
}
