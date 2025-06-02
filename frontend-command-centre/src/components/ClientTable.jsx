import React from 'react';
import { ChevronDown, ChevronUp, Smartphone, HardDrive, Cpu, MemoryStick, Wifi, AlertCircle, CheckCircle2, XCircle, PowerOff, Play, Pause, X } from 'lucide-react';

const getStatusClasses = (clientData) => {
  if (clientData.connected !== 'true') {
    return 'bg-slate-600/30 text-slate-400';
  }
  switch (clientData.client_state) {
    case 'running':
      return 'bg-green-500/20 text-green-400';
    case 'paused':
      return 'bg-yellow-500/20 text-yellow-400';
    case 'error':
      return 'bg-red-500/20 text-red-400';
    default:
      return 'bg-blue-500/20 text-blue-400';
  }
};

const getStatusIcon = (clientData) => {
  if (clientData.connected !== 'true') {
    return <PowerOff size={18} className="mr-2 text-slate-500" />;
  }
  switch (clientData.client_state) {
    case 'running':
      return <CheckCircle2 size={18} className="mr-2" />;
    case 'paused':
      return <AlertCircle size={18} className="mr-2 text-yellow-400" />;
    case 'error':
      return <XCircle size={18} className="mr-2" />;
    default:
      return <Wifi size={18} className="mr-2 text-blue-400" />;
  }
};

const ClientRow = ({ client, onClientAction }) => {
  const [expanded, setExpanded] = React.useState(false);

  const canPause = client.connected === 'true' && client.client_state === 'running';
  const canResume = client.connected === 'true' && client.client_state === 'paused';
  const canDisconnect = client.connected === 'true';

  const handleAction = (e, action) => {
    e.stopPropagation();
    onClientAction(client.client_id, action);
  };

  const displayName = client.client_name || client.client_id || 'N/A';
  const clientIdTooltip = `ID: ${client.client_id || 'Unknown'}`;

  let displayTime = 'N/A';
  if (client.connected !== 'true' && client.disconnect_time) {
    displayTime = new Date(client.disconnect_time).toLocaleString();
  } else if (client.last_seen) {
    displayTime = new Date(client.last_seen).toLocaleString();
  } else if (client.connect_time) {
    displayTime = new Date(client.connect_time).toLocaleString();
  }

  return (
    <>
      <tr
        className={`border-b border-slate-700 hover:bg-slate-700/50 transition-colors duration-150 cursor-pointer ${client.recentlyUpdated ? 'animate-shudder bg-purple-500/10' : ''}`}
        onClick={() => setExpanded(!expanded)}
        tabIndex={0}
        aria-expanded={expanded}
        aria-label={`Client ${displayName}, status ${client.connected === 'true' ? client.client_state || 'connected' : 'disconnected'}. Click to ${expanded ? 'collapse' : 'expand'} details.`}
        onKeyDown={(e) => e.key === 'Enter' && setExpanded(!expanded)}
      >
        <td className="px-4 py-3 whitespace-nowrap">
          <div className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${getStatusClasses(client)}`}>
            {getStatusIcon(client)}
            {client.connected === 'true'
              ? (client.client_state ? client.client_state.charAt(0).toUpperCase() + client.client_state.slice(1) : 'Connected')
              : 'Disconnected'}
          </div>
        </td>
        <td
          className="px-4 py-3 font-medium text-purple-300 whitespace-nowrap"
          title={clientIdTooltip}
        >
          {displayName}
        </td>
        <td className="px-4 py-3 text-slate-400 whitespace-nowrap">{client.client_type || client.type || 'N/A'}</td>
        <td className="px-4 py-3 text-sky-400 whitespace-nowrap">{client.ip_address || 'N/A'}</td>
        <td className="px-4 py-3 text-slate-400 whitespace-nowrap">
          {displayTime}
        </td>
        <td className="px-4 py-3 whitespace-nowrap text-right">
          {canPause && (
            <button
              onClick={(e) => handleAction(e, 'pause')}
              className="p-1.5 text-yellow-400 hover:text-yellow-300 disabled:text-slate-600"
              aria-label={`Pause client ${client.client_id}`}
              title="Pause Client"
            >
              <Pause size={18} />
            </button>
          )}
          {canResume && (
            <button
              onClick={(e) => handleAction(e, 'resume')}
              className="p-1.5 text-green-400 hover:text-green-300 disabled:text-slate-600"
              aria-label={`Resume client ${client.client_id}`}
              title="Resume Client"
            >
              <Play size={18} />
            </button>
          )}
          {canDisconnect && (
            <button
              onClick={(e) => handleAction(e, 'disconnect')}
              className="p-1.5 text-red-500 hover:text-red-400 disabled:text-slate-600 ml-1"
              aria-label={`Disconnect client ${client.client_id}`}
              title="Disconnect Client"
            >
              <XCircle size={18} />
            </button>
          )}
        </td>
        <td className="px-4 py-3 text-slate-300 whitespace-nowrap">
          {expanded ? <ChevronUp size={20} /> : <ChevronDown size={20} />}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-slate-750">
          <td colSpan={6} className="p-0">
            <div className="p-4 grid grid-cols-1 md:grid-cols-3 gap-4 bg-slate-700/30">
              <div className="flex items-center space-x-2 p-3 bg-slate-800 rounded-md">
                <Cpu size={20} className="text-teal-400" />
                <div>
                  <p className="text-xs text-slate-400">CPU Usage</p>
                  <p className="text-sm font-semibold text-teal-300">{client.cpu_usage || 'N/A'}</p>
                </div>
              </div>
              <div className="flex items-center space-x-2 p-3 bg-slate-800 rounded-md">
                <MemoryStick size={20} className="text-orange-400" />
                <div>
                  <p className="text-xs text-slate-400">Memory</p>
                  <p className="text-sm font-semibold text-orange-300">{client.memory_usage || 'N/A'}</p>
                </div>
              </div>
              <div className="flex items-center space-x-2 p-3 bg-slate-800 rounded-md">
                <HardDrive size={20} className="text-indigo-400" />
                <div>
                  <p className="text-xs text-slate-400">Disk Space</p>
                  <p className="text-sm font-semibold text-indigo-300">{client.disk_usage || 'N/A'}</p>
                </div>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
};

const ClientTable = ({ clients, isLoading, error, /* redisStatus, */ /* wsStatus, */ onClientAction }) => {
  if (isLoading) {
    return <p className="text-center text-slate-400 py-8">Loading client data...</p>;
  }

  if (error) {
    return <p className="text-center text-red-400 py-8">Error fetching data: {error}</p>;
  }

  const clientList = Object.entries(clients || {}).map(([id, data]) => ({
    ...data,
    client_id: id
  })).sort((a, b) => {
    // Handle cases where last_seen might be missing or invalid to prevent runtime errors
    const dateA = a.last_seen ? new Date(a.last_seen) : new Date(0); // Fallback to epoch if missing
    const dateB = b.last_seen ? new Date(b.last_seen) : new Date(0); // Fallback to epoch if missing

    // Check for invalid dates after parsing
    if (isNaN(dateA.getTime())) return 1; // Push items with invalid dates to the end
    if (isNaN(dateB.getTime())) return -1;

    return dateB - dateA; // Sorts in descending order (most recent first)
  });

  if (clientList.length === 0) {
    return (
      <div className="bg-slate-800 p-6 rounded-xl shadow-xl flex-grow flex flex-col min-h-[400px]">
        <h2 className="text-2xl font-semibold text-purple-400 mb-6">Registered Clients</h2>
        <p className="text-center text-slate-400 py-8">No client data available.</p>
      </div>
    );
  }

  return (
    <div className="bg-slate-800 p-6 rounded-xl shadow-xl flex flex-col h-[61.8vh]">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-2xl font-semibold text-purple-400">Registered Clients</h2>
      </div>
      <div className="overflow-x-auto overflow-y-auto flex-grow rounded-lg scrollbar-thin scrollbar-thumb-slate-600 scrollbar-track-slate-700">
        <table className="min-w-full divide-y divide-slate-700 border border-slate-600 rounded-lg overflow-hidden">
          <thead className="bg-slate-700/70 sticky top-0 z-10">
            <tr>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Status</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Name</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Type</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">IP Address</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Last Seen</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider">Actions</th>
              <th scope="col" className="px-4 py-3 text-left text-xs font-medium text-slate-300 uppercase tracking-wider"></th>
            </tr>
          </thead>
          <tbody className="bg-slate-800 divide-y divide-slate-700">
            {clientList.map((client) => {
              return <ClientRow key={client.client_id} client={client} onClientAction={onClientAction} />;
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default ClientTable;