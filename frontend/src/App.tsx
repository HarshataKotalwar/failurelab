import { useState } from 'react'
import { DashboardPage } from './pages/DashboardPage'
import { ExperimentDetailPage } from './pages/ExperimentDetailPage'
import { ExperimentsPage } from './pages/ExperimentsPage'
import { InvestigationPage } from './pages/InvestigationPage'

type View = 'dashboard' | 'experiments' | 'detail' | 'investigation'

const crumb: Record<View, string> = {
  dashboard: 'Overview',
  experiments: 'Experiments',
  detail: 'Experiment detail',
  investigation: 'Investigation',
}

export default function App() {
  const [view, setView] = useState<View>('dashboard')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [investigationId, setInvestigationId] = useState<string | null>(null)
  const selectExperiment = (id: string) => { setSelectedId(id); setInvestigationId(null); setView('detail') }
  const openInvestigationResult = (nextInvestigationId: string, experimentId: string) => {
    setInvestigationId(nextInvestigationId)
    setSelectedId(experimentId)
    setView('investigation')
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#dashboard" onClick={(event) => { event.preventDefault(); setView('dashboard') }} aria-label="FailureLab dashboard">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span><strong>FailureLab</strong><small>Incident investigation</small></span>
        </a>
        <nav aria-label="Main navigation">
          <button className={view === 'dashboard' ? 'nav-item active' : 'nav-item'} onClick={() => setView('dashboard')}><span>⌂</span>Overview</button>
          <button className={view === 'experiments' || view === 'detail' ? 'nav-item active' : 'nav-item'} onClick={() => setView('experiments')}><span>◷</span>Experiments</button>
          <button className={view === 'investigation' ? 'nav-item active' : 'nav-item'} onClick={() => { setSelectedId(null); setInvestigationId(null); setView('investigation') }}><span>⌕</span>Investigation</button>
        </nav>
        <div className="sidebar-bottom"><span className="local-indicator" />Local environment<small>Evidence, then conclusion</small></div>
      </aside>
      <main className="main-area">
        <div className="topbar"><div className="breadcrumb">FailureLab <span>/</span> {crumb[view]}</div><span className="local-label">LOCAL STACK</span></div>
        <div className="content-area">
          {view === 'dashboard' && <DashboardPage onSelect={selectExperiment} onExperiments={() => setView('experiments')} onOpenInvestigation={openInvestigationResult} />}
          {view === 'experiments' && <ExperimentsPage onSelect={selectExperiment} />}
          {view === 'detail' && selectedId && (
            <ExperimentDetailPage
              id={selectedId}
              onBack={() => setView('experiments')}
              onSelect={selectExperiment}
              onOpenInvestigation={(nextInvestigationId) => { setInvestigationId(nextInvestigationId ?? null); setView('investigation') }}
            />
          )}
          {view === 'investigation' && (
            <InvestigationPage
              experimentId={selectedId}
              investigationId={investigationId}
              onBack={() => investigationId ? setInvestigationId(null) : setView(selectedId ? 'detail' : 'dashboard')}
              onOpenExperiment={(id) => { setSelectedId(id); setView('detail') }}
              onSelectInvestigation={openInvestigationResult}
            />
          )}
        </div>
      </main>
    </div>
  )
}
